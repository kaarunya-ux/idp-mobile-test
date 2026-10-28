"""The standard driving-licence template: recognised by its words, fields read from their printed captions."""

from datetime import UTC, datetime

import pytest

from extraction.pipeline.application.commands import ExtractDocumentCommand
from extraction.pipeline.application.services.extract_document import ExtractDocumentService
from extraction.pipeline.domain.classify import classify_page
from extraction.pipeline.domain.decisions import Thresholds
from extraction.pipeline.domain.models import PreparationSteps, PreparedPage
from extraction.pipeline.domain.quality import QualityScores
from extraction.pipeline.domain.standard_templates import DRIVING_LICENCE, STANDARD_TEMPLATES
from idp_ocr_engines.layout import RecognizedPiece, assign_line_and_block_ids
from idp_ocr_engines.model import Box, OcrPage, PageImage

DL_LINES = [
    "Indian Union Driving Licence",
    "DL No : MH12 20190012345",
    "Name : ASHA RAO",
    "S/D/W of : RAMESH RAO",
    "DOB : 14-03-1992",
    "Date of Issue : 05-06-2019",
    "Valid Till : 13-03-2032",
    "Blood Group : B+",
    "Address : 12 MG ROAD, PUNE 411001",
    "Class of vehicle MCWG LMV",
]


def _page() -> OcrPage:
    pieces = [
        RecognizedPiece(text=line, box=Box(x=0.05, y=0.05 + 0.09 * n, width=0.8, height=0.05), confidence=0.95)
        for n, line in enumerate(DL_LINES)
    ]
    return OcrPage(width=1586, height=1000, words=assign_line_and_block_ids(pieces))


def test_classifies_a_driving_licence_by_its_printed_words() -> None:
    classification = classify_page(STANDARD_TEMPLATES, page_text=_page().text, width=1586, height=1000)

    assert classification is not None
    assert classification.template is DRIVING_LICENCE


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("license_number", "MH1220190012345"),
        ("name", "ASHA RAO"),
        ("dob", "14-03-1992"),
        ("issue_date", "05-06-2019"),
        ("validity_nt", "13-03-2032"),
        ("blood_group", "B+"),
        ("address", "12 MG ROAD, PUNE 411001"),
        ("vehicle_classes", "MCWG LMV"),
    ],
)
def test_reads_each_field_from_its_caption_when_the_text_is_digital(key: str, value: str) -> None:
    class Clock:
        def now(self) -> datetime:
            return datetime(2026, 10, 28, tzinfo=UTC)

    class Progress:
        def report(self, stage, percent) -> None: ...

    image = PageImage(width=4, height=3, rgb=bytes(36))
    steps = PreparationSteps(warped=False, deskew_degrees=0.0, rotation_degrees=0, orientation_confidence=None)
    page = PreparedPage(page_index=0, clean=image, binarised=image, steps=steps)
    quality = QualityScores(blur=500.0, brightness=0.6, glare=0.0, short_side_px=900, edges_found=True)
    service = ExtractDocumentService(
        engines={},
        templates=STANDARD_TEMPLATES,
        thresholds=Thresholds(name_similarity=0.95, review_confidence=0.85),
        clock=Clock(),
        pipeline_version="t",
        progress=Progress(),
    )

    result = service.extract(ExtractDocumentCommand(pages=(page,), qualities=(quality,), digital_page=_page()))

    fields = {f["key"]: f for f in result["fields"]}
    assert result["documentType"]["code"] == "DRIVING_LICENCE"
    assert (fields[key]["normalizedValue"], fields[key]["status"]) == (value, "ACCEPTED")
