"""What the first pipeline stages need from the outside, in domain language (ports, standard ch. 7)."""

from typing import Protocol

from extraction.pipeline.domain.models import PreparedPage
from extraction.pipeline.domain.quality import QualityScores
from extraction.pipeline.domain.stages import Stage
from idp_ocr_engines.model import PageImage


class PageLoaderPort(Protocol):
    """Checks a file and turns it into page images: PDF pages at 300 DPI, or the image's own pages."""

    def load_pages(self, content: bytes) -> tuple[PageImage, ...]:
        """Raises ``FileRejectedError`` or ``TooManyPagesError`` when the file cannot be accepted (stage SCAN)."""
        ...


class QualityAssessorPort(Protocol):
    """Measures how readable a page is."""

    def assess(self, image: PageImage) -> QualityScores: ...


class PagePreparerPort(Protocol):
    """Straightens and cleans a page for OCR."""

    def prepare(self, image: PageImage) -> PreparedPage: ...


class ProgressReporterPort(Protocol):
    """Tells the outside world how far a stage is (``JobProgressed`` messages are sent from the outbox in step 6)."""

    def report(self, stage: Stage, percent: int) -> None: ...
