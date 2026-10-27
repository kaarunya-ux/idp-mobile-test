"""Small values that describe a job item's files and progress (standard ch. 1.2: value objects)."""

import re
from dataclasses import dataclass
from enum import StrEnum

from conversion.conversion.domain.exceptions import InvalidRequestError
from conversion.conversion.domain.formats import Format

MAX_KEY_LENGTH = 1024  # S3's own limit
_BUCKET = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
_UNSAFE_KEY = re.compile(r"(^/)|(\.\.)|(\\)|[\x00-\x1f\x7f]")  # absolute, parent, backslash or control character


class JobType(StrEnum):
    """The ``type`` of a ``ConversionRequested`` job item (IDP-00 ch. 7.5)."""

    CONVERSION = "CONVERSION"  # files to another format
    PDF_EDIT = "PDF_EDIT"  # one PDF and an edit plan (IDP-03 ch. 3.5)
    PDF_MERGE = "PDF_MERGE"  # several PDFs and images, in order, into one PDF
    EXPORT = "EXPORT"  # extraction results into a file (IDP-03 ch. 3.6)
    PREVIEW = "PREVIEW"  # page thumbnails, written to the derived bucket


@dataclass(frozen=True, slots=True, kw_only=True)
class ThumbnailSpec:
    """How big and how compressed a ``PREVIEW`` thumbnail is (from settings; IDP-00 F-DC-05)."""

    max_side_px: int
    jpeg_quality: int


class Stage(StrEnum):
    """Conversion stages shown to users (IDP-00 ch. 7.5)."""

    PREPARE = "PREPARE"
    CONVERT = "CONVERT"
    PACKAGE = "PACKAGE"


class WorkerPool(StrEnum):
    """The Celery queue a job item runs on, by the resource it needs (IDP-03 ch. 3.2, CEL-05)."""

    OFFICE = "office"  # LibreOffice: few instances, each a whole process
    PDF = "pdf"  # rendering and image work
    DATA = "data"  # tables and documents through the document model
    OCR = "ocr"  # text recognition, on its own hardware


class OutputMode(StrEnum):
    """The ``output`` job option (IDP-02 ch. 3.6): one file, a file per page, or everything in one ZIP."""

    SINGLE = "SINGLE"
    PER_PAGE = "PER_PAGE"
    ZIP = "ZIP"


@dataclass(frozen=True, slots=True, kw_only=True)
class ObjectRef:
    """Where a file lives in object storage. Keys come from Java, so they are checked before any use (FIL-06)."""

    bucket: str
    key: str

    def __post_init__(self) -> None:
        if not _BUCKET.match(self.bucket):
            raise InvalidRequestError(f"'{self.bucket}' is not a valid bucket name.")
        if not 1 <= len(self.key) <= MAX_KEY_LENGTH or _UNSAFE_KEY.search(self.key):
            raise InvalidRequestError("An object key is empty, too long or not a safe relative path.")


@dataclass(frozen=True, slots=True, kw_only=True)
class InputFile:
    ref: ObjectRef
    format: Format


@dataclass(frozen=True, slots=True, kw_only=True)
class StoredOutput:
    """An uploaded output, with what Java stores about it (IDP-00 ch. 7.6, ``ConversionCompleted.outputs``)."""

    ref: ObjectRef
    content_type: str
    size: int
    sha256: str
    page_count: int | None  # pages or slides; None for files without pages (DOCX, XLSX, CSV, ZIP)
