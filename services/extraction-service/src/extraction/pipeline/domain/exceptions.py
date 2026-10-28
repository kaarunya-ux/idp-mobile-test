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


class RequestRejectedError(DomainError):
    """An ``ExtractionRequested`` this service can't act on: bad keys, too many files, a file type it can't read.
    Reported as ``EXT-400-VALIDATION-001``."""

    code: ClassVar[ErrorCode] = ErrorCode.REQUEST_INVALID
    title: ClassVar[str] = "Request can't be processed"


class InputMissingError(DomainError):
    """The file is no longer in storage (deleted, or a guest upload that expired). Reported as ``EXT-422-FILE-001``."""

    code: ClassVar[ErrorCode] = ErrorCode.FILE_REJECTED
    title: ClassVar[str] = "File not found"

    def __init__(self) -> None:
        super().__init__("The file no longer exists in storage.")


class ExtractionTimeoutError(DomainError):
    """The item ran past its time limit. Reported as ``EXT-504-PIPELINE-001``; Java may try again."""

    code: ClassVar[ErrorCode] = ErrorCode.TIMEOUT
    title: ClassVar[str] = "Extraction took too long"
    retryable: ClassVar[bool] = True

    def __init__(self, seconds: float) -> None:
        super().__init__(f"The extraction did not finish within {seconds:.0f} seconds.")


class UnexpectedFailureError(DomainError):
    """Something outside the user's file went wrong after every retry. Reported as ``EXT-500-INTERNAL-001``."""

    code: ClassVar[ErrorCode] = ErrorCode.INTERNAL_ERROR
    title: ClassVar[str] = "Extraction failed"
    retryable: ClassVar[bool] = True

    def __init__(self) -> None:
        super().__init__("The extraction failed for a reason unrelated to the file. Please try again.")


class AiUnavailableError(Exception):
    """The AI fallback could not answer (down, slow, or an answer that wasn't the requested shape). Not a user error:
    the pipeline carries on without it (IDP-03 ch. 2.6: "AI unavailable -> NEEDS_REVIEW, not failure")."""


class TooLargeError(DomainError):
    """The file is bigger than this deployment accepts. Reported as ``EXT-422-FILE-001``."""

    code: ClassVar[ErrorCode] = ErrorCode.FILE_REJECTED
    title: ClassVar[str] = "File too large"
