"""Use case: the synchronous extract (ADR 0009). One document read while the caller waits, OCR first.

The files are checked, then run through the extraction pipeline: the quality gate, both OCR engines, the template's
fields, and for each field the two engines disagree on or are unsure of, its crop read by the AI. When no template
matches, the whole document goes to ai-gateway's ``generic-read``. ``extract_with_ai`` skips OCR: the pages go straight
to ``generic-read`` (the generic route). Pages and crops live in a workspace folder that is
deleted whatever happened: nothing of the document is kept after the answer. Synchronous: it runs in a worker thread.
"""

from extraction.quick.domain.files import check_uploads
from extraction.quick.domain.models import QuickCaller, QuickResult, UploadLimits
from extraction.quick.domain.ports import DocumentPipelinePort, DocumentReaderPort, WorkspacePort


class QuickExtractService:
    def __init__(
        self,
        *,
        workspace: WorkspacePort,
        pipeline: DocumentPipelinePort,
        reader: DocumentReaderPort,
        limits: UploadLimits,
    ) -> None:
        self._workspace = workspace
        self._pipeline = pipeline
        self._reader = reader
        self._limits = limits

    @property
    def max_file_bytes(self) -> int:
        """The route reads no more than this (plus one byte) of each upload."""
        return self._limits.max_file_bytes

    def extract(self, caller: QuickCaller, front: bytes, back: bytes | None) -> QuickResult:
        check_uploads(front, back, self._limits)
        folder = self._workspace.open(caller)
        try:
            outcome = self._pipeline.run(caller, [front] if back is None else [front, back], folder)
            if outcome.result is not None:
                return outcome.result
            return self._reader.read(caller, outcome.pages[:2])  # a card's front and back, or a PDF's first two
        finally:
            self._workspace.discard(folder)

    def extract_with_ai(self, caller: QuickCaller, front: bytes, back: bytes | None) -> QuickResult:
        """Straight to the model: any document's type and every field, read whole by ``generic-read``."""
        check_uploads(front, back, self._limits)
        folder = self._workspace.open(caller)
        try:
            pages = self._pipeline.stage([front] if back is None else [front, back], folder)
            return self._reader.read(caller, pages[:2])
        finally:
            self._workspace.discard(folder)
