"""Stage 7's AI step (IDP-03 ch. 2.6): the fields OCR left unsettled, read again by the AI, from the whole page or
from each field's crop (ADR 0009). Used by ``ExtractDocumentService``."""

import dataclasses
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date

from extraction.pipeline.domain.classify import Classification
from extraction.pipeline.domain.crops import crop_page, padded
from extraction.pipeline.domain.custom_template import CUSTOM_SCOPE
from extraction.pipeline.domain.decisions import FieldDecision, FieldStatus, decide_with_ai
from extraction.pipeline.domain.exceptions import AiUnavailableError
from extraction.pipeline.domain.jobs import ObjectRef
from extraction.pipeline.domain.ports import (
    AiCallContext,
    AiClassifierPort,
    AiCropReaderPort,
    AiFieldCrop,
    AiFieldReaderPort,
    AiReading,
    PageImageStorePort,
)
from extraction.pipeline.domain.template import TemplateDefinition
from idp_ocr_engines.model import Box, PageImage


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
        unresolved = sum(is_unresolved(d) for d in decisions)
        if not unresolved or unresolved > self._limit(template):  # too many: the photo is the problem, not a field
            return decisions, None
        wanted = [d.key for d in decisions if is_unresolved(d) or d.status is FieldStatus.EMPTY]
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


def is_unresolved(decision: FieldDecision) -> bool:
    """Engines A and B did not settle the field: no agreement, so it is NEEDS_REVIEW with no source."""
    return decision.status is FieldStatus.NEEDS_REVIEW and decision.source is None
