"""The files the model-first conversions read and write (IDP-00 ch. 5, IDP-03 ch. 3.4)."""

from enum import StrEnum


class SourceFormat(StrEnum):
    """A kind of file a user converts from."""

    PDF = "PDF"  # digital or scanned: told apart when it is read, from whether a page has a text layer
    DOCX = "DOCX"
    XLSX = "XLSX"
    PPTX = "PPTX"
    CSV = "CSV"
    IMAGE = "IMAGE"  # PNG, JPEG, TIFF, BMP or WebP


class TargetFormat(StrEnum):
    """A kind of file a conversion produces."""

    DOCX = "DOCX"
    XLSX = "XLSX"
    PPTX = "PPTX"
    CSV = "CSV"
    PDF = "PDF"
    IMAGES = "IMAGES"  # one PNG per page


_SUFFIXES: dict[str, SourceFormat] = {
    ".pdf": SourceFormat.PDF,
    ".docx": SourceFormat.DOCX,
    ".xlsx": SourceFormat.XLSX,
    ".pptx": SourceFormat.PPTX,
    ".csv": SourceFormat.CSV,
    ".png": SourceFormat.IMAGE,
    ".jpg": SourceFormat.IMAGE,
    ".jpeg": SourceFormat.IMAGE,
    ".tif": SourceFormat.IMAGE,
    ".tiff": SourceFormat.IMAGE,
    ".bmp": SourceFormat.IMAGE,
    ".webp": SourceFormat.IMAGE,
}


def source_format_of(file_name: str) -> SourceFormat | None:
    """The source format a file name's suffix says, or ``None`` when it is not one the service reads."""
    dot = file_name.rfind(".")
    return _SUFFIXES.get(file_name[dot:].lower()) if dot >= 0 else None
