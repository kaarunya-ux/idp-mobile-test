"""Use case: CLASSIFY, READ, VERIFY and ASSEMBLE for one prepared document (IDP-03 ch. 2.6, stages 4 to 8)."""

import dataclasses
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date

from extraction.core.clock import Clock
from extraction.pipeline.application.commands import ExtractDocumentCommand
from extraction.pipeline.domain.classify import Classification, classify_page
from extraction.pipeline.domain.crops import crop_page, padded
from extraction.pipeline.domain.custom_template import CUSTOM_SCOPE
from extraction.pipeline.domain.decisions import (
    FieldDecision,
    FieldSource,
    FieldStatus,
    Thresholds,
    decide_field,
    decide_with_ai,
)
from extraction.pipeline.domain.exceptions import AiUnavailableError
from extraction.pipeline.domain.jobs import ObjectRef
from extraction.pipeline.domain.models import PreparedPage
from extraction.pipeline.domain.ports import (
    AiCallContext,
    AiClassifierPort,
    AiCropReaderPort,
    AiFieldCrop,
    AiFieldReaderPort,
    AiReading,
    PageImageStorePort,
    ProgressReporterPort,
)
from extraction.pipeline.domain.quality import QualityScores
from extraction.pipeline.domain.reading import Reading, field_box, read_field, shift_for
from extraction.pipeline.domain.result import PageSummary, Provenance, build_result
from extraction.pipeline.domain.stages import Stage
from extraction.pipeline.domain.template import FieldDefinition, TemplateDefinition
from idp_ocr_engines.errors import OcrEngineError
from idp_ocr_engines.model import Box, OcrPage, PageImage
from idp_ocr_engines.port import OcrEnginePort

_DONE = 100
_ENGLISH = ("en",)
_TIE_BREAKER = "C"
_DIGITAL = "digital"  # the slot name for a PDF's text layer


@dataclass(frozen=True, slots=True, kw_only=True)
class AiCrops:
    """Stage 7 with crops (ADR 0009): each unsure field is cut out of the page, stored, and read alone by the AI."""

    reader: AiCropReaderPort
    store: PageImageStorePort
    padding: float = 0.5  # the crop's margin, in field heights (AI_GATEWAY__CROP_PADDING)
    workers: int = 3  # crops read at the same time (AI_GATEWAY__CROP_CONCURRENCY)


@dataclass(frozen=True, slots=True, kw_only=True)
class AiAssist:
    """Stage 7's AI step. With ``crops``, each field the two engines left unsure is cut out and read alone; without,
    every unsettled field goes to ``reader`` in one request on the whole page (``AI_GATEWAY__READ_MODE=page``)."""

    reader: AiFieldReaderPort
    confidence: float  # given to an AI value that passes its rule (settings: AI_GATEWAY__CONFIDENCE)
    max_fields: int = 10  # more unsettled fields than this go to review instead (AI_GATEWAY__MAX_FIELDS_PER_DOCUMENT)
    classifier: AiClassifierPort | None = None
    classify_min_confidence: float = 0.8  # an AI classification below this leaves the document unknown
    crops: AiCrops | None = None

    def _limit(self, template: TemplateDefinition) -> int:
        """How many unsettled fields still go to the AI. A custom template's keys are found by their labels only, and a
        key whose label isn't printed is normal there, not a sign of a bad photo: all of them may go."""
        return len(template.fields) if template.scope == CUSTOM_SCOPE else self.max_fields

    def classify(
        self, templates: Sequence[TemplateDefinition], context: AiCallContext
    ) -> tuple[Classification | None, tuple[str, str] | None]:
        """Stage 4 for a page the keywords didn't match: ask the AI which candidate type it is. Returns the
        template for that type (the account's own first, as the catalog orders them) and who answered."""
        if self.classifier is None or not templates or not context.page_refs:
            return None, None
        candidates = list(dict.fromkeys(t.document_type for t in templates))
        try:
            answer = self.classifier.classify(context.page_refs[0], candidates, context)
        except AiUnavailableError:
            return None, None
        provenance = (answer.model_id, answer.prompt_version)
        if answer.document_type is None or answer.confidence < self.classify_min_confidence:
            return None, provenance
        template = next(t for t in templates if t.document_type == answer.document_type)
        return Classification(template=template, confidence=answer.confidence), provenance

    def assist(
        self,
        template: TemplateDefinition,
        decisions: list[FieldDecision],
        pages: Sequence[PageImage],
        today: date,
        context: AiCallContext,
    ) -> tuple[list[FieldDecision], tuple[str, str] | None]:
        """The decisions with the unsettled ones decided again by AI, and the (model, prompt version) that answered.

        The AI is asked only when a field is unsettled (OCR first). Once it is asked, optional fields OCR found empty
        go in the same request at no extra cost; an AI value for them still has to pass the field's rule. No answer
        (gateway busy, model down, cap used) leaves the fields as OCR left them, and isn't counted as an AI call.
        """
        unresolved = sum(_is_unresolved(d) for d in decisions)
        if not unresolved or unresolved > self._limit(template):  # too many: the photo is the problem, not a field
            return decisions, None
        wanted = [d.key for d in decisions if _is_unresolved(d) or d.status is FieldStatus.EMPTY]
        definitions = {d.key: d for d in template.fields}
        labelled = dataclasses.replace(context, labels={key: definitions[key].label for key in wanted})
        try:
            reading = self.reader.read_fields(template.document_type, pages, wanted, labelled)
        except AiUnavailableError:
            return decisions, None
        values = reading.values
        return [
            decide_with_ai(definitions[d.key], d, values.get(d.key), confidence=self.confidence, today=today)
            if d.key in wanted
            else d
            for d in decisions
        ], (reading.model_id, reading.prompt_version)

    def assist_with_crops(
        self,
        template: TemplateDefinition,
        decisions: list[FieldDecision],
        page: PageImage,
        boxes: Mapping[str, Box],
        today: date,
        context: AiCallContext,
    ) -> tuple[list[FieldDecision], list[tuple[str, str]]]:
        """The decisions with every unsure field read again by AI from its crop, and the (model, prompt version)
        pairs that answered.

        Unsure is NEEDS_REVIEW: the engines disagreed, agreed below the review confidence, or missed a required field.
        The crop is the area the engines read the field in (moved with its anchor), with a margin; the engines'
        readings go along as hints. An AI value still has to pass the field's rule. A crop without an answer leaves
        that field as OCR left it.
        """
        crops, folder = self.crops, context.crop_folder
        unsure = [d for d in decisions if d.status is FieldStatus.NEEDS_REVIEW]
        if crops is None or folder is None or not unsure or len(unsure) > self._limit(template):
            return decisions, []
        definitions = {d.key: d for d in template.fields}

        def read(decision: FieldDecision) -> tuple[str, AiReading | None]:
            ref = ObjectRef(bucket=folder.bucket, key=f"{folder.key}/{decision.key}.png")
            crops.store.save_page(ref, crop_page(page, padded(boxes[decision.key], padding=crops.padding)))
            definition = definitions[decision.key]
            crop = AiFieldCrop(
                key=decision.key,
                label=definition.label,
                type=definition.type.value,
                crop=ref,
                readings=decision.readings,
            )
            try:
                return decision.key, crops.reader.read_crop(crop, context)
            except AiUnavailableError:
                return decision.key, None

        with ThreadPoolExecutor(max_workers=min(crops.workers, len(unsure))) as pool:
            answers = dict(pool.map(read, unsure))
        decided = [
            decide_with_ai(definitions[d.key], d, found.values.get(d.key), confidence=self.confidence, today=today)
            if (found := answers.get(d.key)) is not None
            else d
            for d in decisions
        ]
        return decided, sorted({(a.model_id, a.prompt_version) for a in answers.values() if a is not None})


class ExtractDocumentService:
    """Reads the first page, picks the matching template, and decides each field (IDP-03 ch. 2.6, stages 5 to 7).

    ``engines`` maps the slot (``"A"``, ``"B"``, optionally ``"C"``) to an engine. A and B read the whole page at the
    same time (an engine that fails is retried once, then left out). Each field is decided from A and B: accepted when
    they agree and the rule passes. Engine C is asked only if some field is unresolved: it then reads the page once and
    each unresolved field is decided again with two of three. (IDP-03 says C reads the field crop; Indra read crops
    badly, see ADR 0003.) Slot A also reads for CLASSIFY. ``languages`` says what each slot is asked to read (default
    English only): a field in another language is read only by the slots that read that language. Fields still
    unresolved go to the AI in one request (``AiAssist``); without an answer they stay NEEDS_REVIEW. A page the
    keywords don't match goes to the AI's classify when one is configured.
    """

    # every collaborator is a port or a number
    def __init__(  # noqa: PLR0913
        self,
        *,
        engines: Mapping[str, OcrEnginePort],
        templates: Sequence[TemplateDefinition],
        thresholds: Thresholds,
        clock: Clock,
        pipeline_version: str,
        languages: Mapping[str, Sequence[str]] | None = None,
        progress: ProgressReporterPort,
        monotonic: Callable[[], float] = time.monotonic,
        ai: AiAssist | None = None,
    ) -> None:
        self._engines = engines
        self._languages = {slot: tuple((languages or {}).get(slot, _ENGLISH)) for slot in engines}
        self._templates = templates
        self._thresholds = thresholds
        self._clock = clock
        self._pipeline_version = pipeline_version
        self._progress = progress
        self._monotonic = monotonic
        self._ai = ai

    def extract(self, command: ExtractDocumentCommand) -> dict[str, object]:
        """The result JSON (IDP-00 ch. 7.8). Raises ``OcrEngineError`` only if no engine could read the page."""
        started = self._monotonic()
        page = command.pages[0]
        pages, digital = self._read(command, page.clean)
        self._progress.report(Stage.READ, _DONE)
        first = pages[sorted(pages)[0]]
        classification, models = self._classify(command, first.text, page.clean)
        self._progress.report(Stage.CLASSIFY, _DONE)
        decisions, boxes = (
            self._decide(classification.template, pages, page.clean, digital=digital) if classification else ([], {})
        )
        self._progress.report(Stage.VERIFY, _DONE)
        if classification:
            decisions, answered = self._ai_step(command, classification.template, decisions, boxes)
            models += answered
        self._progress.report(Stage.AI_ASSIST, _DONE)
        result = build_result(
            classification=classification,
            pages=[_summary(p, q) for p, q in zip(command.pages, command.qualities, strict=True)],
            decisions=decisions,
            provenance=Provenance(
                pipeline_version=self._pipeline_version,
                engines=[("digital-text", "pdfium")]
                if digital
                else [(f"ocr-{slot.lower()}", self._engines[slot].engine_version) for slot in sorted(self._engines)],
                duration_ms=round((self._monotonic() - started) * 1000),
                models=models,
            ),
        )
        self._progress.report(Stage.ASSEMBLE, _DONE)
        return dict(result)

    def _classify(
        self, command: ExtractDocumentCommand, text: str, page: PageImage
    ) -> tuple[Classification | None, list[tuple[str, str]]]:
        """The template named in the request; else keywords and shape; else, for an unknown page, the AI."""
        if command.template_forced and len(self._templates) == 1:
            return Classification(template=self._templates[0], confidence=1.0), []
        found = classify_page(self._templates, page_text=text, width=page.width, height=page.height)
        if found is not None or self._ai is None or command.ai_context is None:
            return found, []
        found, answered = self._ai.classify(self._templates, command.ai_context)
        return found, [answered] if answered else []

    def _read(self, command: ExtractDocumentCommand, image: PageImage) -> tuple[dict[str, OcrPage], bool]:
        """A digital PDF's text layer is exact: it is read instead of OCR (IDP-03 ch. 2.6 stage 5), as both readings,
        but only when it shows which document this is (a template's keywords, or the template named in the request).
        A scan inside a PDF can carry a few words of text, such as a header or a stamp, while the document itself is
        an image: that layer would hide the page from OCR and leave every field to the AI. Then OCR reads the page."""
        digital = command.digital_page
        if digital is not None and (
            command.template_forced
            or classify_page(self._templates, page_text=digital.text, width=digital.width, height=digital.height)
            is not None
        ):
            return {_DIGITAL: digital}, True
        return self._read_pages(image), False

    def _ai_step(
        self,
        command: ExtractDocumentCommand,
        template: TemplateDefinition,
        decisions: list[FieldDecision],
        boxes: Mapping[str, Box],
    ) -> tuple[list[FieldDecision], list[tuple[str, str]]]:
        """Stage 7: the unsure fields' crops when crops are set up (ADR 0009), else the whole page in one request."""
        if self._ai is None or command.ai_context is None:
            return decisions, []
        today = self._clock.now().date()
        if self._ai.crops is not None and command.ai_context.crop_folder is not None:
            page = command.pages[0].clean  # the fields are read on the first page
            return self._ai.assist_with_crops(template, decisions, page, boxes, today, command.ai_context)
        sides = [p.clean for p in command.pages[:2]]  # a card's front and back
        context = dataclasses.replace(command.ai_context, page_refs=command.ai_context.page_refs[:2])
        decided, answered = self._ai.assist(template, decisions, sides, today, context)
        return decided, [answered] if answered else []

    def _read_pages(self, image: PageImage) -> dict[str, OcrPage]:
        """Every engine except the tie-breaker reads the page, all at once; one that fails twice is left out."""
        slots = [slot for slot in sorted(self._engines) if slot != _TIE_BREAKER]
        with ThreadPoolExecutor(max_workers=len(slots)) as pool:
            futures = {slot: pool.submit(self._read_with_retry, slot, image) for slot in slots}
            results = {slot: future.result() for slot, future in futures.items()}
        pages = {slot: page for slot, page in results.items() if page is not None}
        if not pages:
            message = "No OCR engine could read the page."
            raise OcrEngineError(message)
        return pages

    def _read_with_retry(self, slot: str, image: PageImage) -> OcrPage | None:
        for _attempt in range(2):
            try:
                return self._engines[slot].read_page(image, self._languages[slot])
            except OcrEngineError:
                continue
        return None

    def _decide(
        self, template: TemplateDefinition, pages: Mapping[str, OcrPage], image: PageImage, *, digital: bool = False
    ) -> tuple[list[FieldDecision], dict[str, Box]]:
        """Each field's decision, and the box it was looked for in (moved with its anchor), for the AI's crops."""
        today = self._clock.now().date()
        shifts = {}
        for anchor in template.anchors:
            found = shift_for(anchor, pages[sorted(pages)[0]].words)
            if found is not None:
                shifts[anchor.id] = found
        decisions: list[FieldDecision] = []
        boxes: dict[str, Box] = {}
        slots = [*pages, *([_TIE_BREAKER] if _TIE_BREAKER in self._engines else [])]
        used: dict[str, dict[str, Box]] = {slot: {} for slot in slots}  # slot -> field key -> where it was read
        tie_page = _LazyPage(lambda: self._read_tie_breaker(image))
        for definition in template.fields:
            box = boxes[definition.key] = field_box(definition, shifts)
            if digital:
                decisions.append(self._decide_digital(definition, pages[_DIGITAL], box, used, today))
                continue
            readings: dict[str, Reading | None] = {
                slot: self._read_field(definition, ocr, slot, box, used, today) for slot, ocr in pages.items()
            }
            decision = decide_field(definition, readings, thresholds=self._thresholds, today=today)
            if _is_unresolved(decision) and definition.language in self._languages.get(_TIE_BREAKER, ()):
                page_c = tie_page.get()
                if page_c is not None:
                    readings[_TIE_BREAKER] = self._read_field(definition, page_c, _TIE_BREAKER, box, used, today)
                    decision = decide_field(definition, readings, thresholds=self._thresholds, today=today)
            decisions.append(decision)
        return decisions, boxes

    def _decide_digital(
        self,
        definition: FieldDefinition,
        page: OcrPage,
        box: Box,
        used: dict[str, dict[str, Box]],
        today: date,
    ) -> FieldDecision:
        """The text layer read once and counted as both readings: they agree by construction, so the rule alone
        decides. Every language is read: the text layer has them all."""
        skip = [used[_DIGITAL][key] for key in definition.excludes if key in used.setdefault(_DIGITAL, {})]
        reading = read_field(definition, page.words, box, today=today, skip=skip, page_aspect=page.width / page.height)
        if reading is not None:
            used[_DIGITAL][definition.key] = reading.box
        decision = decide_field(definition, {"A": reading, "B": reading}, thresholds=self._thresholds, today=today)
        if decision.source is FieldSource.OCR_AGREE:
            decision = dataclasses.replace(decision, source=FieldSource.DIGITAL_TEXT)
        return dataclasses.replace(decision, readings={"digital": decision.readings.get("A")})

    def _read_field(
        self,
        definition: FieldDefinition,
        ocr: OcrPage,
        slot: str,
        box: Box,
        used: dict[str, dict[str, Box]],
        today: date,
    ) -> Reading | None:
        if definition.language not in self._languages[slot]:
            return None  # this engine cannot read the field's language
        skip = [used[slot][key] for key in definition.excludes if key in used[slot]]
        reading = read_field(definition, ocr.words, box, today=today, skip=skip, page_aspect=ocr.width / ocr.height)
        if reading is not None:
            used[slot][definition.key] = reading.box
        return reading

    def _read_tie_breaker(self, image: PageImage) -> OcrPage | None:
        try:
            return self._engines[_TIE_BREAKER].read_page(image, self._languages[_TIE_BREAKER])
        except OcrEngineError:
            return None


class _LazyPage:
    """Engine C's page, read the first time it is needed and not at all if every field is settled by A and B."""

    def __init__(self, read: Callable[[], OcrPage | None]) -> None:
        self._read = read
        self._done = False
        self._page: OcrPage | None = None

    def get(self) -> OcrPage | None:
        if not self._done:
            self._page, self._done = self._read(), True
        return self._page


def _is_unresolved(decision: FieldDecision) -> bool:
    """Engines A and B did not settle the field: no agreement, so it is NEEDS_REVIEW with no source."""
    return decision.status is FieldStatus.NEEDS_REVIEW and decision.source is None


def _summary(page: PreparedPage, quality: QualityScores) -> PageSummary:
    return PageSummary(
        index=page.page_index,
        width=page.clean.width,
        height=page.clean.height,
        image_key=None,
        quality=quality,
        quality_passed=True,
    )
