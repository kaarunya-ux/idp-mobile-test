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


IMAGE_FORMATS: frozenset[Format] = frozenset({Format.PNG, Format.JPEG})


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
