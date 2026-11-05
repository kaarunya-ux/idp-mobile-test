"""Use case: the synchronous extract (ADR 0009). One document read while the caller waits, OCR first.

The files are checked, then run through the extraction pipeline: both OCR engines, the template's fields, and the AI
for the fields the engines disagree on or are unsure of. When no template matches, the whole document goes to
ai-gateway's ``generic-read``. ``extract_custom`` reads the keys a user defined (ADR 0013): a template built from them
for this request, the same pipeline. ``extract_with_ai`` skips OCR: the pages go straight to ``generic-read``. Pages
and crops are held in memory for the request and dropped whatever happened (ADR 0012). Synchronous: it runs in a
worker thread.
"""

from collections.abc import Sequence

from extraction.pipeline.domain.custom_template import MAX_FIELDS, CustomField, custom_template
from extraction.quick.domain.exceptions import FieldsInvalidError
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

    def extract_custom(
        self, caller: QuickCaller, front: bytes, back: bytes | None, fields: Sequence[CustomField]
    ) -> QuickResult:
        """Only the given keys, each read by its label, OCR first."""
        if not 1 <= len(fields) <= MAX_FIELDS or len({f.key for f in fields}) != len(fields):
            raise FieldsInvalidError(f"Send 1 to {MAX_FIELDS} fields, each key once.")
        check_uploads(front, back, self._limits)
        folder = self._workspace.open(caller)
        try:
            contents = [front] if back is None else [front, back]
            outcome = self._pipeline.run(caller, contents, folder, custom_template(fields))
            if outcome.result is None:  # the template is named, so this doesn't happen: kept explicit
                raise FieldsInvalidError("The fields could not be read with this document.")
            return outcome.result
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
