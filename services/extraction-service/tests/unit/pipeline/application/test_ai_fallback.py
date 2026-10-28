"""The AI fallback (stage 7): unsettled fields go to the AI reader; its answer counts only if the rule passes."""

import threading
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

import pytest

from extraction.pipeline.application.commands import ExtractDocumentCommand
from extraction.pipeline.application.services.extract_document import AiAssist, ExtractDocumentService
from extraction.pipeline.domain.decisions import Thresholds
from extraction.pipeline.domain.exceptions import AiUnavailableError
from extraction.pipeline.domain.jobs import ObjectRef
from extraction.pipeline.domain.models import PreparationSteps, PreparedPage
from extraction.pipeline.domain.ports import AiCallContext, AiClassification, AiReading
from extraction.pipeline.domain.quality import QualityScores
from extraction.pipeline.domain.stages import Stage
from extraction.pipeline.domain.standard_templates import STANDARD_TEMPLATES
from idp_ocr_engines.errors import OcrEngineError
from idp_ocr_engines.model import Box, OcrPage, OcrRegion, OcrWord, PageImage

CONTEXT = AiCallContext(
    account_id=UUID("0192f0aa-0000-7000-8000-000000000001"),
    priority=True,
    page_refs=tuple(ObjectRef(bucket="idp-derived", key=f"acct/doc/item/page-{i}.png") for i in range(3)),
)
PAN_LINES = [
    (0.05, 0.12, "INCOME TAX DEPARTMENT"),
    (0.20, 0.30, "Permanent Account Number Card"),
    (0.30, 0.40, "ABCPE1234F"),
    (0.03, 0.52, "/Name"),
    (0.03, 0.58, "RAMESH SAHANI"),
    (0.03, 0.68, "/Father's Name"),
    (0.03, 0.74, "SURESH SAHANI"),
    (0.03, 0.86, "Date of Birth"),
    (0.03, 0.92, "27/02/2002"),
]


class FakeEngine:
    """An engine that "reads" fixed lines. It counts its calls and can fail or wait for another engine."""

    def __init__(
        self,
        slot: str,
        lines: Sequence[tuple[float, float, str]],
        confidence: float = 0.95,
        *,
        failures: int = 0,
        barrier: threading.Barrier | None = None,
    ) -> None:
        self.engine_id = slot
        self.engine_version = f"fake-{slot}@1"
        self._lines = lines
        self._confidence = confidence
        self._failures = failures
        self._barrier = barrier
        self.page_reads = 0
        self.region_reads = 0

    def warm_up(self) -> None: ...

    def read_page(self, image: PageImage, languages: Sequence[str]) -> OcrPage:
        if self._barrier is not None:
            self._barrier.wait(timeout=5)  # only passes if the other engine is reading at the same time
        self.page_reads += 1
        if self._failures >= self.page_reads:
            message = "the engine failed"
            raise OcrEngineError(message)
        words = tuple(
            OcrWord(
                text=text,
                box=Box(x=x, y=y, width=0.3, height=0.04),
                confidence=self._confidence,
                line_id=i,
                block_id=0,
            )
            for i, (x, y, text) in enumerate(self._lines)
        )
        return OcrPage(width=image.width, height=image.height, words=words)

    def read_region(self, image: PageImage, box: Box, languages: Sequence[str]) -> OcrRegion:
        self.region_reads += 1
        inside = [
            text
            for x, y, text in self._lines
            if box.x - 0.01 <= x + 0.15 <= box.x + box.width + 0.01
            and box.y - 0.01 <= y + 0.02 <= box.y + box.height + 0.01
        ]
        return OcrRegion(box=box, text=" ".join(inside), confidence=self._confidence)


class Progress:
    def __init__(self) -> None:
        self.stages: list[Stage] = []

    def report(self, stage: Stage, percent: int) -> None:
        self.stages.append(stage)


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 10, 21, tzinfo=UTC)


def command() -> ExtractDocumentCommand:
    image = PageImage(width=86, height=54, rgb=bytes(86 * 54 * 3))
    page = PreparedPage(
        page_index=0,
        clean=image,
        binarised=image,
        steps=PreparationSteps(warped=False, deskew_degrees=0.0, rotation_degrees=0, orientation_confidence=None),
    )
    quality = QualityScores(blur=500.0, brightness=0.6, glare=0.0, short_side_px=900, edges_found=True)
    return ExtractDocumentCommand(pages=(page,), qualities=(quality,), ai_context=CONTEXT)


def service(
    engines: dict[str, FakeEngine],
    progress: Progress,
    languages: dict[str, tuple[str, ...]] | None = None,
    ai: AiAssist | None = None,
) -> ExtractDocumentService:
    ticks = iter([0.0, 1.5])
    return ExtractDocumentService(
        engines=engines,
        languages=languages,
        templates=STANDARD_TEMPLATES,
        thresholds=Thresholds(name_similarity=0.95, review_confidence=0.85),
        clock=FixedClock(),
        pipeline_version="0.1.0",
        progress=progress,
        monotonic=lambda: next(ticks),
        ai=ai,
    )


def other_pan(number: str) -> list[tuple[float, float, str]]:
    return [(x, y, number if text == "ABCPE1234F" else text) for x, y, text in PAN_LINES]


def fields_of(result: dict[str, object]) -> dict[str, dict[str, object]]:
    return {f["key"]: f for f in result["fields"]}  # type: ignore[attr-defined]


class FakeAi:
    def __init__(self, answer: dict[str, str | None] | None = None, *, down: bool = False) -> None:
        self.answer, self.down, self.asked = answer or {}, down, []
        self.contexts: list[AiCallContext] = []

    def read_fields(
        self, document_type: str, pages: Sequence[PageImage], keys: Sequence[str], context: AiCallContext
    ) -> AiReading:
        self.asked.append((document_type, tuple(keys)))
        self.contexts.append(context)
        if self.down:
            raise AiUnavailableError("down")
        values = {key: self.answer.get(key) for key in keys}
        return AiReading(values=values, model_id="vl-test", prompt_version="field-read.v1")


def _disagreeing_engines() -> dict[str, FakeEngine]:
    return {"A": FakeEngine("A", PAN_LINES), "B": FakeEngine("B", other_pan("ABCPE9999Z"))}


def test_accepts_the_ai_value_when_the_engines_disagree_and_the_rule_passes() -> None:
    ai = FakeAi({"pan_number": "ABCPE1234F"})

    result = service(_disagreeing_engines(), Progress(), ai=AiAssist(reader=ai, confidence=0.9)).extract(command())

    pan = fields_of(result)["pan_number"]
    assert ai.asked == [("PAN_CARD", ("pan_number",))]  # only the unsettled field, one request
    assert (pan["value"], pan["source"], pan["status"], pan["confidence"]) == ("ABCPE1234F", "AI", "ACCEPTED", 0.9)
    assert pan["readings"]["ai"] == "ABCPE1234F"
    assert result["provenance"]["models"] == [{"id": "vl-test", "promptVersion": "field-read.v1"}]


def test_sends_the_account_priority_stored_page_keys_and_labels_when_asking_the_ai() -> None:
    ai = FakeAi({"pan_number": "ABCPE1234F"})

    service(_disagreeing_engines(), Progress(), ai=AiAssist(reader=ai, confidence=0.9)).extract(command())

    sent = ai.contexts[0]
    assert (sent.account_id, sent.priority, sent.page_refs) == (CONTEXT.account_id, True, CONTEXT.page_refs[:2])
    assert sent.labels["pan_number"]


def test_skips_the_ai_when_the_command_has_no_ai_context() -> None:
    ai = FakeAi({"pan_number": "ABCPE1234F"})
    base = command()

    result = service(_disagreeing_engines(), Progress(), ai=AiAssist(reader=ai, confidence=0.9)).extract(
        ExtractDocumentCommand(pages=base.pages, qualities=base.qualities)
    )

    assert ai.asked == []
    assert fields_of(result)["pan_number"]["status"] == "NEEDS_REVIEW"


def test_keeps_the_field_for_review_when_the_ai_value_fails_the_rule() -> None:
    ai = FakeAi({"pan_number": "NOT-A-PAN"})

    result = service(_disagreeing_engines(), Progress(), ai=AiAssist(reader=ai, confidence=0.9)).extract(command())

    pan = fields_of(result)["pan_number"]
    assert (pan["status"], pan["source"], pan["readings"]["ai"]) == ("NEEDS_REVIEW", None, "NOT-A-PAN")


def test_keeps_the_field_for_review_and_does_not_fail_when_the_ai_is_down() -> None:
    result = service(_disagreeing_engines(), Progress(), ai=AiAssist(reader=FakeAi(down=True), confidence=0.9)).extract(
        command()
    )

    assert fields_of(result)["pan_number"]["status"] == "NEEDS_REVIEW"
    assert result["provenance"]["models"] == []  # no answer: not counted as an AI call


def test_never_asks_the_ai_when_the_engines_settle_every_field() -> None:
    ai = FakeAi()
    engines = {"A": FakeEngine("A", PAN_LINES), "B": FakeEngine("B", PAN_LINES)}

    result = service(engines, Progress(), ai=AiAssist(reader=ai, confidence=0.9)).extract(command())

    assert ai.asked == []
    assert result["provenance"]["models"] == []


def _two_sided_command() -> ExtractDocumentCommand:
    single = command()
    back = PreparedPage(
        page_index=1,
        clean=PageImage(width=86, height=54, rgb=bytes(86 * 54 * 3), page_index=1),
        binarised=single.pages[0].binarised,
        steps=single.pages[0].steps,
    )
    return ExtractDocumentCommand(
        pages=(single.pages[0], back), qualities=(single.qualities[0],) * 2, ai_context=CONTEXT
    )


def test_sends_both_sides_and_the_empty_optional_fields_once_the_ai_is_asked() -> None:
    class Recording(FakeAi):
        def read_fields(self, document_type, pages, keys, context):
            self.sides = len(pages)
            return super().read_fields(document_type, pages, keys, context)

    ai = Recording({"pan_number": "ABCPE1234F"})

    service(_disagreeing_engines(), Progress(), ai=AiAssist(reader=ai, confidence=0.9)).extract(_two_sided_command())

    assert ai.sides == 2
    (_, keys) = ai.asked[0]
    assert "pan_number" in keys


def test_reads_a_digital_pdf_from_its_text_layer_without_ocr() -> None:
    engines = {"A": FakeEngine("A", PAN_LINES), "B": FakeEngine("B", PAN_LINES)}
    digital = engines["A"].read_page(command().pages[0].clean, ("en",))  # the same words, as a text layer gives them
    engines["A"].page_reads = 0
    base = command()

    result = service(engines, Progress()).extract(
        ExtractDocumentCommand(pages=base.pages, qualities=base.qualities, digital_page=digital)
    )

    pan = fields_of(result)["pan_number"]
    assert (engines["A"].page_reads, engines["B"].page_reads) == (0, 0)  # no OCR at all
    assert (pan["value"], pan["source"], pan["status"]) == ("ABCPE1234F", "DIGITAL_TEXT", "ACCEPTED")
    assert result["provenance"]["engines"] == [{"id": "digital-text", "version": "pdfium"}]


class FakeClassifier:
    def __init__(self, document_type: str | None, confidence: float = 0.9, *, down: bool = False) -> None:
        self.document_type, self.confidence, self.down, self.asked = document_type, confidence, down, []

    def classify(self, page: ObjectRef, candidates: Sequence[str], context: AiCallContext) -> AiClassification:
        self.asked.append((page, list(candidates)))
        if self.down:
            raise AiUnavailableError("down")
        return AiClassification(
            document_type=self.document_type, confidence=self.confidence, model_id="vl", prompt_version="classify.v1"
        )


def _unknown_page_engines() -> dict[str, FakeEngine]:
    lines = [(0.1, 0.2, "SOMETHING"), (0.1, 0.4, "ABCPE1234F"), (0.1, 0.6, "27/02/2002")]
    return {"A": FakeEngine("A", lines), "B": FakeEngine("B", lines)}


def test_asks_the_ai_which_type_it_is_when_the_keywords_match_nothing() -> None:
    classifier = FakeClassifier("PAN_CARD")
    ai = AiAssist(reader=FakeAi(), confidence=0.9, classifier=classifier)

    result = service(_unknown_page_engines(), Progress(), ai=ai).extract(command())

    assert result["documentType"] == {"code": "PAN_CARD", "confidence": 0.9}
    assert classifier.asked[0][0] == CONTEXT.page_refs[0]
    assert "PAN_CARD" in classifier.asked[0][1]
    assert {"id": "vl", "promptVersion": "classify.v1"} in result["provenance"]["models"]


@pytest.mark.parametrize(
    "classifier", [FakeClassifier("PAN_CARD", 0.5), FakeClassifier(None), FakeClassifier("X", down=True)]
)
def test_leaves_the_document_unknown_when_the_ai_is_unsure_or_down(classifier: FakeClassifier) -> None:
    ai = AiAssist(reader=FakeAi(), confidence=0.9, classifier=classifier)

    result = service(_unknown_page_engines(), Progress(), ai=ai).extract(command())

    assert result["documentType"]["code"] is None
    assert result["fields"] == []


def test_does_not_ask_the_ai_when_more_fields_are_unsettled_than_allowed() -> None:
    reader = FakeAi({"pan_number": "ABCPE1234F"})

    service(_disagreeing_engines(), Progress(), ai=AiAssist(reader=reader, confidence=0.9, max_fields=0)).extract(
        command()
    )

    assert reader.asked == []


def test_uses_the_named_template_without_classifying_when_the_request_names_one() -> None:
    pan = next(t for t in STANDARD_TEMPLATES if t.document_type == "PAN_CARD")
    base = command()
    forced = ExtractDocumentCommand(pages=base.pages, qualities=base.qualities, template_forced=True)
    engines = _unknown_page_engines()
    extractor = ExtractDocumentService(
        engines=engines,
        templates=[pan],
        thresholds=Thresholds(name_similarity=0.95, review_confidence=0.85),
        clock=FixedClock(),
        pipeline_version="0.1.0",
        progress=Progress(),
    )

    result = extractor.extract(forced)

    assert result["documentType"] == {"code": "PAN_CARD", "confidence": 1.0}
