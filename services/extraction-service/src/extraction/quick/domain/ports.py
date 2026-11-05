"""What the synchronous extract needs from the outside, in domain language (ports, standard ch. 7).

All synchronous: the use case runs in a worker thread of the sync process, never on its event loop (ASY-01).
"""

from collections.abc import Sequence
from typing import Protocol

from extraction.pipeline.domain.template import TemplateDefinition
from extraction.quick.domain.models import PipelineOutcome, QuickCaller, QuickResult, StagedFile


class WorkspacePort(Protocol):
    """A folder in the derived bucket for one call's pages and crops, deleted when the call ends."""

    def open(self, caller: QuickCaller) -> str:
        """A new folder (a key prefix) under the caller's account."""
        ...

    def discard(self, folder: str) -> None:
        """Deletes everything in it; a failure is logged, never raised (the bucket's lifecycle rule is the backstop)."""
        ...


class DocumentPipelinePort(Protocol):
    """The extraction pipeline on the uploaded files: quality gate, two OCR engines, the template's fields, and the AI
    on the crops of the fields the engines left unsure. Raises the pipeline's file and quality errors."""

    def run(
        self,
        caller: QuickCaller,
        contents: Sequence[bytes],
        folder: str,
        template: TemplateDefinition | None = None,
    ) -> PipelineOutcome:
        """With ``template`` (a custom template), the document is read with it, without classifying; else with the
        account's templates, the one that matches."""
        ...

    def stage(self, contents: Sequence[bytes], folder: str) -> Sequence[StagedFile]:
        """The files as page images (PDF pages rendered, photos upright), no OCR and no quality gate: the first two
        stored in ``folder``, for the straight-to-the-model extract. Raises the pipeline's file errors."""
        ...


class DocumentReaderPort(Protocol):
    """ai-gateway's ``generic-read`` on the INTERACTIVE lane, for a document no template matched."""

    def read(self, caller: QuickCaller, pages: Sequence[StagedFile]) -> QuickResult:
        """Raises ``BusyError``, ``AiLimitReachedError``, ``AiUnavailableError``, or ``FileUnreadableError``."""
        ...
