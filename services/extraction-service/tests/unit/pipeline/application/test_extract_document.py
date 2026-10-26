import threading
from collections.abc import Sequence
from datetime import UTC, datetime

import pytest

from extraction.pipeline.application.commands import ExtractDocumentCommand
from extraction.pipeline.application.services.extract_document import ExtractDocumentService
from extraction.pipeline.domain.decisions import Thresholds
from extraction.pipeline.domain.models import PreparationSteps, PreparedPage
from extraction.pipeline.domain.quality import QualityScores
from extraction.pipeline.domain.stages import Stage
from extraction.pipeline.domain.standard_templates import STANDARD_TEMPLATES
from idp_ocr_engines.errors import OcrEngineError
from idp_ocr_engines.model import Box, OcrPage, OcrRegion, OcrWord, PageImage

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
    return ExtractDocumentCommand(pages=(page,), qualities=(quality,))


def service(
    engines: dict[str, FakeEngine], progress: Progress, languages: dict[str, tuple[str, ...]] | None = None
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
    )


def other_pan(number: str) -> list[tuple[float, float, str]]:
    return [(x, y, number if text == "ABCPE1234F" else text) for x, y, text in PAN_LINES]


def fields_of(result: dict[str, object]) -> dict[str, dict[str, object]]:
    return {f["key"]: f for f in result["fields"]}  # type: ignore[attr-defined]


def test_a_pan_card_read_identically_by_a_and_b_is_fully_accepted_and_c_is_never_asked() -> None:
    progress = Progress()
    engine_c = FakeEngine("ocr-c", PAN_LINES)
    engines = {"A": FakeEngine("ocr-a", PAN_LINES), "B": FakeEngine("ocr-b", PAN_LINES), "C": engine_c}
    result = service(engines, progress).extract(command())
    assert result["documentType"] == {"code": "PAN_CARD", "confidence": 0.6667}
    assert result["validation"] == {"fieldsTotal": 4, "accepted": 4, "needsReview": 0, "failedRules": []}
    fields = fields_of(result)
    assert fields["pan_number"]["value"] == "ABCPE1234F"
    assert fields["pan_number"]["source"] == "OCR_AGREE"
    assert fields["dob"]["readings"]["A"] == "27/02/2002"  # type: ignore[index]
    assert result["provenance"]["durationMs"] == 1500  # type: ignore[index]
    assert result["provenance"]["engines"] == [  # type: ignore[index]
        {"id": "ocr-a", "version": "fake-ocr-a@1"},
        {"id": "ocr-b", "version": "fake-ocr-b@1"},
        {"id": "ocr-c", "version": "fake-ocr-c@1"},
    ]
    assert progress.stages == [Stage.READ, Stage.CLASSIFY, Stage.VERIFY, Stage.AI_ASSIST, Stage.ASSEMBLE]
    assert engine_c.page_reads == 0
    assert engine_c.region_reads == 0


def test_a_and_b_read_the_page_at_the_same_time() -> None:
    barrier = threading.Barrier(2)  # each engine waits here until the other is also reading
    engines = {
        "A": FakeEngine("ocr-a", PAN_LINES, barrier=barrier),
        "B": FakeEngine("ocr-b", PAN_LINES, barrier=barrier),
    }
    result = service(engines, Progress()).extract(command())
    assert result["validation"]["accepted"] == 4  # type: ignore[index]


def test_c_reads_the_page_once_only_when_a_field_is_unresolved_and_breaks_the_tie() -> None:
    engine_c = FakeEngine("ocr-c", PAN_LINES)
    engines = {"A": FakeEngine("ocr-a", PAN_LINES), "B": FakeEngine("ocr-b", other_pan("ABCPE1235F")), "C": engine_c}
    result = service(engines, Progress()).extract(command())
    pan = fields_of(result)["pan_number"]
    assert pan["status"] == "ACCEPTED"
    assert pan["source"] == "OCR_2_OF_3"
    assert pan["value"] == "ABCPE1234F"
    assert pan["readings"]["C"] == "ABCPE1234F"  # type: ignore[index]
    assert engine_c.page_reads == 1  # one read of the page, for the one unresolved field
    assert engine_c.region_reads == 0  # crops are not used: Indra reads them badly


def test_when_no_two_engines_agree_the_field_needs_review_with_the_most_confident_passing_reading() -> None:
    engines = {
        "A": FakeEngine("ocr-a", PAN_LINES, 0.90),
        "B": FakeEngine("ocr-b", other_pan("ABCPE1235F"), 0.92),
        "C": FakeEngine("ocr-c", other_pan("ABCPE1236F"), 0.97),
    }
    result = service(engines, Progress()).extract(command())
    pan = fields_of(result)["pan_number"]
    assert pan["status"] == "NEEDS_REVIEW"
    assert pan["source"] is None
    assert pan["value"] == "ABCPE1236F"


def test_without_engine_c_disagreement_goes_straight_to_review() -> None:
    engines = {"A": FakeEngine("ocr-a", PAN_LINES), "B": FakeEngine("ocr-b", other_pan("ABCPE1235F"))}
    result = service(engines, Progress()).extract(command())
    assert fields_of(result)["pan_number"]["status"] == "NEEDS_REVIEW"
    assert result["validation"]["needsReview"] == 1  # type: ignore[index]


def test_an_engine_that_fails_once_is_retried() -> None:
    engine_a = FakeEngine("ocr-a", PAN_LINES, failures=1)
    result = service({"A": engine_a, "B": FakeEngine("ocr-b", PAN_LINES)}, Progress()).extract(command())
    assert engine_a.page_reads == 2
    assert result["validation"]["accepted"] == 4  # type: ignore[index]


def test_an_engine_that_fails_twice_is_left_out_and_c_settles_the_fields_with_a() -> None:
    engines = {
        "A": FakeEngine("ocr-a", PAN_LINES),
        "B": FakeEngine("ocr-b", PAN_LINES, failures=2),
        "C": FakeEngine("ocr-c", PAN_LINES),
    }
    result = service(engines, Progress()).extract(command())
    fields = fields_of(result)
    assert all(f["status"] == "ACCEPTED" for f in fields.values())
    assert all(f["source"] == "OCR_2_OF_3" for f in fields.values())  # A and C, since B is gone


def test_when_no_engine_can_read_the_page_the_error_is_raised() -> None:
    engines = {"A": FakeEngine("ocr-a", PAN_LINES, failures=2), "B": FakeEngine("ocr-b", PAN_LINES, failures=2)}
    with pytest.raises(OcrEngineError):
        service(engines, Progress()).extract(command())


def test_an_unknown_document_has_no_fields_and_needs_review() -> None:
    blank = [(0.1, 0.1, "Invoice 42")]
    result = service({"A": FakeEngine("ocr-a", blank), "B": FakeEngine("ocr-b", blank)}, Progress()).extract(command())
    assert result["documentType"] == {"code": None, "confidence": 0.0}
    assert result["template"] is None
    assert result["fields"] == []
    assert result["validation"]["needsReview"] == 1  # type: ignore[index]


def test_a_hindi_field_is_read_by_b_and_settled_by_c_because_a_cannot_read_hindi() -> None:
    lines = [
        (0.1, 0.10, "Government of India"),
        (0.1, 0.20, "Aadhaar"),
        (0.1, 0.40, "\u092a\u094d\u0930\u0915\u093e\u0936 \u0930\u093e\u0920\u094b\u0921"),
        (0.1, 0.50, "Prakash Rathod"),
        (0.1, 0.60, "DOB: 01/07/1974"),
    ]
    engine_c = FakeEngine("ocr-c", lines)
    engines = {"A": FakeEngine("ocr-a", lines), "B": FakeEngine("ocr-b", lines), "C": engine_c}
    result = service(engines, Progress(), {"B": ("en", "hi"), "C": ("en", "hi")}).extract(command())
    fields = fields_of(result)
    name_hi = fields["name_hi"]
    assert name_hi["readings"]["A"] is None  # type: ignore[index]
    assert name_hi["readings"]["B"] is not None  # type: ignore[index]
    assert name_hi["readings"]["C"] == name_hi["readings"]["B"]  # type: ignore[index]
    assert name_hi["status"] == "ACCEPTED"
    assert name_hi["source"] == "OCR_2_OF_3"
    assert fields["name"]["readings"]["A"] == "Prakash Rathod"  # type: ignore[index]
    assert engine_c.page_reads == 1  # the Hindi fields were unresolved, so C was asked once


def test_a_field_in_a_language_c_does_not_read_is_not_sent_to_c() -> None:
    lines = [
        (0.1, 0.40, "\u092a\u094d\u0930\u0915\u093e\u0936"),
        (0.1, 0.60, "DOB: 01/07/1974"),
        (0.1, 0.1, "Aadhaar"),
        (0.1, 0.2, "Government of India"),
    ]
    engine_c = FakeEngine("ocr-c", lines)
    engines = {"A": FakeEngine("ocr-a", lines), "B": FakeEngine("ocr-b", lines), "C": engine_c}
    result = service(engines, Progress(), {"B": ("en", "hi")}).extract(command())  # C reads English only
    assert fields_of(result)["name_hi"]["status"] == "NEEDS_REVIEW"
