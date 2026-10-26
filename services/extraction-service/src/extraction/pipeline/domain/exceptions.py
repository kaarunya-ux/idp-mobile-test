"""Business errors of the pipeline's first stages. Each carries the code that reaches the user (IDP-03 ch. 2.12)."""

from typing import ClassVar

from extraction.core.error_codes import ErrorCode
from extraction.core.errors import DomainError
from extraction.pipeline.domain.quality import QualityIssue


class FileRejectedError(DomainError):
    """The file is not a supported, readable document (stage SCAN)."""

    code: ClassVar[ErrorCode] = ErrorCode.FILE_REJECTED
    title: ClassVar[str] = "File cannot be read"


class TooManyPagesError(DomainError):
    """The document has more pages than this deployment accepts."""

    code: ClassVar[ErrorCode] = ErrorCode.PAGES_LIMIT_EXCEEDED
    title: ClassVar[str] = "Too many pages"


class QualityRejectedError(DomainError):
    """A page is too poor to read. The user is asked to upload a better photo or scan."""

    title: ClassVar[str] = "Image quality is too low"


class ImageBlurredError(QualityRejectedError):
    code: ClassVar[ErrorCode] = ErrorCode.QUALITY_BLURRED


class ImageTooDarkError(QualityRejectedError):
    code: ClassVar[ErrorCode] = ErrorCode.QUALITY_TOO_DARK


class ImageGlareError(QualityRejectedError):
    code: ClassVar[ErrorCode] = ErrorCode.QUALITY_GLARE


class ImageResolutionError(QualityRejectedError):
    code: ClassVar[ErrorCode] = ErrorCode.QUALITY_RESOLUTION


class DocumentEdgesNotFoundError(QualityRejectedError):
    code: ClassVar[ErrorCode] = ErrorCode.QUALITY_EDGES_NOT_FOUND


_BY_ISSUE: dict[QualityIssue, tuple[type[QualityRejectedError], str]] = {
    QualityIssue.BLURRED: (ImageBlurredError, "is blurred. Hold the camera steady or upload a sharper scan."),
    QualityIssue.TOO_DARK: (ImageTooDarkError, "is too dark. Take the photo in better light."),
    QualityIssue.TOO_BRIGHT: (ImageGlareError, "is too bright. Avoid direct light on the document."),
    QualityIssue.GLARE: (ImageGlareError, "has glare on it. Tilt the document away from the light."),
    QualityIssue.LOW_RESOLUTION: (
        ImageResolutionError,
        "has too low a resolution. Move closer or use a higher quality.",
    ),
    QualityIssue.EDGES_NOT_FOUND: (
        DocumentEdgesNotFoundError,
        "does not show the whole document. Place it on a plain surface.",
    ),
}


def quality_error_for(issue: QualityIssue, page_index: int) -> QualityRejectedError:
    """The error to raise for ``issue`` on a page (counted from 1 in the message, as people count pages)."""
    error_type, advice = _BY_ISSUE[issue]
    return error_type(f"Page {page_index + 1} {advice}")
