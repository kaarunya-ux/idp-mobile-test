"""The file formats IDP converts between, and what flows between conversion steps (IDP-00 ch. 5, IDP-03 ch. 3.4)."""

from enum import StrEnum
from typing import Self


class Format(StrEnum):
    """A file format a user converts from or to."""

    PDF = "PDF"
    DOCX = "DOCX"
    XLSX = "XLSX"
    CSV = "CSV"
    PPTX = "PPTX"
    PNG = "PNG"
    JPEG = "JPEG"

    @property
    def is_image(self) -> bool:
        return self in IMAGE_FORMATS

    @property
    def content_type(self) -> str:
        """The MIME type stored with an output in MinIO and reported to Java."""
        return _CONTENT_TYPES[self]

    @property
    def suffix(self) -> str:
        return _SUFFIXES[self]

    @classmethod
    def from_content_type(cls, content_type: str) -> Self | None:
        """The format of an input, from the type document-service detected on upload (IDP-02 ch. 3.3)."""
        bare = content_type.split(";", 1)[0].strip().lower()  # "text/csv; charset=utf-8" -> "text/csv"
        for member, known in _CONTENT_TYPES.items():
            if known == bare:
                return cls(member)
        alias = _CONTENT_TYPE_ALIASES.get(bare)
        return cls(alias) if alias is not None else None


IMAGE_FORMATS: frozenset[Format] = frozenset({Format.PNG, Format.JPEG})
ZIP_CONTENT_TYPE = "application/zip"
_OFFICE = "application/vnd.openxmlformats-officedocument"
_CONTENT_TYPES = {
    Format.PDF: "application/pdf",
    Format.DOCX: f"{_OFFICE}.wordprocessingml.document",
    Format.XLSX: f"{_OFFICE}.spreadsheetml.sheet",
    Format.CSV: "text/csv",
    Format.PPTX: f"{_OFFICE}.presentationml.presentation",
    Format.PNG: "image/png",
    Format.JPEG: "image/jpeg",
}
_CONTENT_TYPE_ALIASES = {"image/jpg": Format.JPEG, "application/csv": Format.CSV}  # seen from older clients
_SUFFIXES = {
    Format.PDF: ".pdf",
    Format.DOCX: ".docx",
    Format.XLSX: ".xlsx",
    Format.CSV: ".csv",
    Format.PPTX: ".pptx",
    Format.PNG: ".png",
    Format.JPEG: ".jpg",
}


class Representation(StrEnum):
    """What one conversion step reads or writes: a file format, or the document model.

    The document model (pages → text blocks, lines and tables, with positions) is the data hub of IDP-03 ch. 3.4:
    every format is read *into* the model and written *out of* it, so N formats need about 2N converters
    instead of N x N.
    """

    PDF = "PDF"
    DOCX = "DOCX"
    XLSX = "XLSX"
    CSV = "CSV"
    PPTX = "PPTX"
    PNG = "PNG"
    JPEG = "JPEG"
    DOCUMENT_MODEL = "DOCUMENT_MODEL"

    @classmethod
    def of(cls, file_format: Format) -> Self:
        """The representation of a file in ``file_format``."""
        return cls(file_format.value)
