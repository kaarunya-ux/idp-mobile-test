"""The prepare use case with scripted ports: stage order, progress, and stopping at the first bad page."""

import numpy as np
import pytest

from extraction.core.error_codes import ErrorCode
from extraction.pipeline.application.commands import PrepareDocumentCommand
from extraction.pipeline.application.services.prepare_document import PrepareDocumentService
from extraction.pipeline.domain.exceptions import FileRejectedError, QualityRejectedError
from extraction.pipeline.domain.models import PreparationSteps, PreparedPage
from extraction.pipeline.domain.quality import QualityScores, QualityThresholds
from extraction.pipeline.domain.stages import Stage
from idp_ocr_engines.engines.arrays import from_rgb_array
from idp_ocr_engines.model import PageImage

THRESHOLDS = QualityThresholds(
    blur_min=100.0,
    brightness_min=0.25,
    brightness_max=0.90,
    glare_max=0.04,
    min_short_side_px=500,
    require_document_edges=True,
)
GOOD = QualityScores(blur=400.0, brightness=0.6, glare=0.0, short_side_px=800, edges_found=True)
BLURRED = QualityScores(blur=10.0, brightness=0.6, glare=0.0, short_side_px=800, edges_found=True)


def _page(index: int) -> PageImage:
    return from_rgb_array(np.zeros((4, 4, 3), dtype=np.uint8), page_index=index)


class FakeLoader:
    def __init__(self, pages: int = 2, error: Exception | None = None) -> None:
        self._pages, self._error = pages, error

    def load_pages(self, content: bytes) -> tuple[PageImage, ...]:
        if self._error:
            raise self._error
        return tuple(_page(index) for index in range(self._pages))


class FakeAssessor:
    def __init__(self, scores: list[QualityScores]) -> None:
        self._scores, self.calls = scores, 0

    def assess(self, image: PageImage) -> QualityScores:
        self.calls += 1
        return self._scores[image.page_index]


class FakePreparer:
    def __init__(self) -> None:
        self.prepared: list[int] = []

    def prepare(self, image: PageImage) -> PreparedPage:
        self.prepared.append(image.page_index)
        steps = PreparationSteps(warped=False, deskew_degrees=0.0, rotation_degrees=0, orientation_confidence=None)
        return PreparedPage(page_index=image.page_index, clean=image, binarised=image, steps=steps)


class RecordingProgress:
    def __init__(self) -> None:
        self.events: list[tuple[Stage, int]] = []

    def report(self, stage: Stage, percent: int) -> None:
        self.events.append((stage, percent))


def _service(
    loader: FakeLoader, assessor: FakeAssessor, preparer: FakePreparer, progress: RecordingProgress
) -> PrepareDocumentService:
    return PrepareDocumentService(
        loader=loader, assessor=assessor, preparer=preparer, thresholds=THRESHOLDS, progress=progress
    )


def test_prepares_every_page_and_reports_each_stage_when_all_pages_pass() -> None:
    preparer, progress = FakePreparer(), RecordingProgress()

    result = _service(FakeLoader(2), FakeAssessor([GOOD, GOOD]), preparer, progress).prepare(
        PrepareDocumentCommand(content=b"x")
    )

    assert [page.page_index for page in result.pages] == [0, 1]
    assert result.qualities == (GOOD, GOOD)
    assert progress.events == [
        (Stage.SCAN, 0),
        (Stage.SCAN, 100),
        (Stage.QUALITY_CHECK, 50),
        (Stage.QUALITY_CHECK, 100),
        (Stage.PREPARE, 50),
        (Stage.PREPARE, 100),
    ]


def test_stops_at_the_first_bad_page_and_prepares_nothing_when_a_page_fails_the_gate() -> None:
    assessor, preparer = FakeAssessor([GOOD, BLURRED]), FakePreparer()

    with pytest.raises(QualityRejectedError) as caught:
        _service(FakeLoader(2), assessor, preparer, RecordingProgress()).prepare(PrepareDocumentCommand(content=b"x"))

    assert caught.value.code is ErrorCode.QUALITY_BLURRED
    assert caught.value.detail.startswith("Page 2 ")
    assert preparer.prepared == []


def test_does_not_assess_later_pages_when_an_earlier_page_already_failed() -> None:
    assessor = FakeAssessor([BLURRED, GOOD])

    with pytest.raises(QualityRejectedError):
        _service(FakeLoader(2), assessor, FakePreparer(), RecordingProgress()).prepare(
            PrepareDocumentCommand(content=b"x")
        )

    assert assessor.calls == 1


def test_lets_a_file_error_through_when_the_loader_rejects_the_file() -> None:
    assessor = FakeAssessor([])

    with pytest.raises(FileRejectedError):
        _service(FakeLoader(error=FileRejectedError("no")), assessor, FakePreparer(), RecordingProgress()).prepare(
            PrepareDocumentCommand(content=b"x")
        )

    assert assessor.calls == 0
