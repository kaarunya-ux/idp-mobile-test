"""Use case: CLASSIFY, READ, VERIFY and ASSEMBLE for one prepared document (IDP-03 ch. 2.6, stages 4 to 8)."""

import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date

from extraction.core.clock import Clock
from extraction.pipeline.application.commands import ExtractDocumentCommand
from extraction.pipeline.domain.classify import classify_page
from extraction.pipeline.domain.decisions import FieldDecision, FieldStatus, Thresholds, decide_field, decide_with_ai
from extraction.pipeline.domain.exceptions import AiUnavailableError
from extraction.pipeline.domain.models import PreparedPage
from extraction.pipeline.domain.ports import AiFieldReaderPort, ProgressReporterPort
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


@dataclass(frozen=True, slots=True, kw_only=True)
class AiAssist:
    """Stage 7's AI step: every field the engines left unsettled goes to the AI reader in one request."""

    reader: AiFieldReaderPort
    confidence: float  # given to an AI value that passes its rule (settings: AI_FALLBACK__CONFIDENCE)

    @property
    def provenance(self) -> tuple[str, str]:
        return (self.reader.model_id, self.reader.prompt_version)

    def assist(
        self, template: TemplateDefinition, decisions: list[FieldDecision], page: PageImage, today: date
    ) -> tuple[list[FieldDecision], bool]:
        """The decisions with the unsettled ones decided again by AI, and whether the AI was asked at all."""
        unsettled = [d.key for d in decisions if _is_unresolved(d)]
        if not unsettled:
            return decisions, False
        try:
            values = self.reader.read_fields(template.document_type, page, unsettled)
        except AiUnavailableError:
            return decisions, True  # asked, no answer: the fields stay NEEDS_REVIEW
        definitions = {d.key: d for d in template.fields}
        return [
            decide_with_ai(definitions[d.key], d, values.get(d.key), confidence=self.confidence, today=today)
            if d.key in unsettled
            else d
            for d in decisions
        ], True


class ExtractDocumentService:
    """Reads the first page, picks the matching template, and decides each field (IDP-03 ch. 2.6, stages 5 to 7).

    ``engines`` maps the slot (``"A"``, ``"B"``, optionally ``"C"``) to an engine. A and B read the whole page at the
    same time (an engine that fails is retried once, then left out). Each field is decided from A and B: accepted when
    they agree and the rule passes. Engine C is asked only if some field is unresolved: it then reads the page once and
    each unresolved field is decided again with two of three. (IDP-03 says C reads the field crop; Indra read crops
    badly, see ADR 0003.) Slot A also reads for CLASSIFY. ``languages`` says what each slot is asked to read (default
    English only): a field in another language is read only by the slots that read that language. AI_ASSIST is not
    built, so a field that is still unresolved stays NEEDS_REVIEW.
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
        pages = self._read_pages(page.clean)
        self._progress.report(Stage.READ, _DONE)
        first = pages[sorted(pages)[0]]
        classification = classify_page(
            self._templates, page_text=first.text, width=page.clean.width, height=page.clean.height
        )
        self._progress.report(Stage.CLASSIFY, _DONE)
        decisions = self._decide(classification.template, pages, page.clean) if classification else []
        self._progress.report(Stage.VERIFY, _DONE)
        models: list[tuple[str, str]] = []
        if classification and self._ai is not None:
            decisions, asked = self._ai.assist(classification.template, decisions, page.clean, self._clock.now().date())
            models = [self._ai.provenance] if asked else []
        self._progress.report(Stage.AI_ASSIST, _DONE)
        result = build_result(
            classification=classification,
            pages=[_summary(p, q) for p, q in zip(command.pages, command.qualities, strict=True)],
            decisions=decisions,
            provenance=Provenance(
                pipeline_version=self._pipeline_version,
                engines=[(f"ocr-{slot.lower()}", self._engines[slot].engine_version) for slot in sorted(self._engines)],
                duration_ms=round((self._monotonic() - started) * 1000),
                models=models,
            ),
        )
        self._progress.report(Stage.ASSEMBLE, _DONE)
        return dict(result)

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
        self, template: TemplateDefinition, pages: Mapping[str, OcrPage], image: PageImage
    ) -> list[FieldDecision]:
        today = self._clock.now().date()
        shifts = {}
        for anchor in template.anchors:
            found = shift_for(anchor, pages[sorted(pages)[0]].words)
            if found is not None:
                shifts[anchor.id] = found
        decisions: list[FieldDecision] = []
        slots = [*pages, *([_TIE_BREAKER] if _TIE_BREAKER in self._engines else [])]
        used: dict[str, dict[str, Box]] = {slot: {} for slot in slots}  # slot -> field key -> where it was read
        tie_page = _LazyPage(lambda: self._read_tie_breaker(image))
        for definition in template.fields:
            box = field_box(definition, shifts)
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
        return decisions

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
