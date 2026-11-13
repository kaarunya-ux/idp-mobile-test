"""Use case: the synchronous extract (ADR 0009). One document read while the caller waits, OCR first.

The files are checked, then run through the extraction pipeline: both OCR engines, the template's fields, and the AI
for the fields the engines disagree on or are unsure of. When no template matches, the whole document goes to
ai-gateway's ``generic-read``. ``extract_custom`` reads the keys a user defined (ADR 0013): a template built from them
for this request, the same pipeline. ``extract_with_ai`` skips the OCR pipeline: the pages go straight to
``generic-read``, while one OCR engine finds where each value is printed, so every generic field comes back with its
box on the pages as uploaded. Pages and crops are held in memory for the request and dropped whatever happened (ADR
0012). Synchronous: it runs in a worker thread.
"""

from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from extraction.pipeline.domain.custom_template import MAX_FIELDS, CustomField, custom_template
from extraction.quick.domain.exceptions import FieldsInvalidError
from extraction.quick.domain.files import check_uploads
from extraction.quick.domain.lines import number_lines, result_from_lines
from extraction.quick.domain.locate import place_fields
from extraction.quick.domain.models import GenericMode, QuickCaller, QuickResult, StagedFile, UploadLimits
from extraction.quick.domain.ports import DocumentPipelinePort, DocumentReaderPort, PageWordsPort, WorkspacePort
from idp_ocr_engines.errors import OcrEngineError


class QuickExtractService:
    def __init__(
        self,
        *,
        workspace: WorkspacePort,
        pipeline: DocumentPipelinePort,
        reader: DocumentReaderPort,
        words: PageWordsPort,
        limits: UploadLimits,
        generic_mode: GenericMode = GenericMode.PICTURE,
    ) -> None:
        self._workspace = workspace
        self._pipeline = pipeline
        self._reader = reader
        self._words = words
        self._generic_mode = generic_mode
        self._limits = limits

    @property
    def max_file_bytes(self) -> int:
        """The route reads no more than this (plus one byte) of each upload."""
        return self._limits.max_file_bytes

    def extract(self, caller: QuickCaller, front: bytes, back: bytes | None) -> QuickResult:
        check_uploads(front, back, self._limits)
        folder = self._workspace.open(caller)
        try:
            contents = [front] if back is None else [front, back]
            outcome = self._pipeline.run(caller, contents, folder)
            if outcome.result is not None:
                return outcome.result
            return self._generic(
                caller, contents, outcome.pages[:2], folder
            )  # a card's front and back, or a PDF's first two
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
            contents = [front] if back is None else [front, back]
            if self._generic_mode is GenericMode.LINES:
                return self._generic(caller, contents, (), folder)  # OCR's lines only: no page image is sent
            pages = self._pipeline.stage(contents, folder)
            return self._generic(caller, contents, pages[:2], folder)
        finally:
            self._workspace.discard(folder)

    def _generic(
        self, caller: QuickCaller, contents: Sequence[bytes], pages: Sequence[StagedFile], folder: str
    ) -> QuickResult:
        """The generic read, in the configured mode (ADR 0015); a page OCR finds no text on is read as a picture."""
        if self._generic_mode is GenericMode.LINES:
            lines = number_lines(self._words.read_words(contents))
            if lines:
                return result_from_lines(self._reader.read_lines(caller, [line.text for line in lines]), lines)
            if not pages:
                pages = self._pipeline.stage(contents, folder)[:2]
        return self._by_picture(caller, contents, pages)

    def _by_picture(self, caller: QuickCaller, contents: Sequence[bytes], pages: Sequence[StagedFile]) -> QuickResult:
        """The model reads every field; meanwhile OCR finds where words are printed, so each value gets its box on
        the pages as uploaded (no added wait: OCR takes a second or two, the model far longer)."""
        with ThreadPoolExecutor(max_workers=1) as pool:
            words = pool.submit(self._words.read_words, contents)
            result = self._reader.read(caller, pages)
            try:
                found = words.result()
            except OcrEngineError:  # the boxes are an extra: without them the fields still go back
                return result
            return replace(result, fields=place_fields(result.fields, found))
