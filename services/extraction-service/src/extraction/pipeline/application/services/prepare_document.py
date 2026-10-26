"""Use case: SCAN, QUALITY_CHECK and PREPARE for one uploaded file (the ``prepare`` task of IDP-03 ch. 2.6)."""

from extraction.pipeline.application.commands import PrepareDocumentCommand
from extraction.pipeline.application.results import PrepareDocumentResult
from extraction.pipeline.domain.exceptions import quality_error_for
from extraction.pipeline.domain.models import PreparedPage
from extraction.pipeline.domain.ports import PageLoaderPort, PagePreparerPort, ProgressReporterPort, QualityAssessorPort
from extraction.pipeline.domain.quality import QualityScores, QualityThresholds, list_quality_issues
from extraction.pipeline.domain.stages import Stage

_DONE = 100


class PrepareDocumentService:
    """Loads the pages, refuses any that are too poor to read, and prepares the rest for OCR.

    Runs in a Celery worker, so it is synchronous (standard ch. 15.1). It stops at the first page that fails the quality
    gate: the user is asked to upload a better file, so reading the other pages would be wasted work.
    """

    def __init__(
        self,
        *,
        loader: PageLoaderPort,
        assessor: QualityAssessorPort,
        preparer: PagePreparerPort,
        thresholds: QualityThresholds,
        progress: ProgressReporterPort,
    ) -> None:
        self._loader = loader
        self._assessor = assessor
        self._preparer = preparer
        self._thresholds = thresholds
        self._progress = progress

    def prepare(self, command: PrepareDocumentCommand) -> PrepareDocumentResult:
        """Raises ``FileRejectedError``, ``TooManyPagesError`` or a ``QualityRejectedError`` for the first bad page."""
        self._progress.report(Stage.SCAN, 0)
        images = self._loader.load_pages(command.content)
        self._progress.report(Stage.SCAN, _DONE)

        qualities: list[QualityScores] = []
        for position, image in enumerate(images):
            scores = self._assessor.assess(image)
            issues = list_quality_issues(scores, self._thresholds)
            if issues:
                raise quality_error_for(issues[0], image.page_index)
            qualities.append(scores)
            self._progress.report(Stage.QUALITY_CHECK, _percent(position + 1, len(images)))

        prepared: list[PreparedPage] = []
        for position, image in enumerate(images):
            prepared.append(self._preparer.prepare(image))
            self._progress.report(Stage.PREPARE, _percent(position + 1, len(images)))
        return PrepareDocumentResult(pages=tuple(prepared), qualities=tuple(qualities))


def _percent(done: int, total: int) -> int:
    return done * _DONE // total
