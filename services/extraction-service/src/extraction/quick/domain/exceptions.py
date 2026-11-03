"""Business errors of the synchronous extract (ADR 0009). The details are safe to show the person (ERR-06)."""

from typing import ClassVar

from extraction.core.error_codes import ErrorCode
from extraction.core.errors import DomainError


class FileUnreadableError(DomainError):
    code: ClassVar[ErrorCode] = ErrorCode.FILE_REJECTED
    title: ClassVar[str] = "File cannot be read"


class FileTooLargeError(DomainError):
    code: ClassVar[ErrorCode] = ErrorCode.FILE_TOO_LARGE
    title: ClassVar[str] = "File too large"


class FileTypeUnsupportedError(DomainError):
    code: ClassVar[ErrorCode] = ErrorCode.FILE_TYPE_UNSUPPORTED
    title: ClassVar[str] = "File type not supported"


class NoAccountError(DomainError):
    """A token without an account (platform staff, a user still onboarding): no allowance to count the call on."""

    code: ClassVar[ErrorCode] = ErrorCode.FORBIDDEN
    title: ClassVar[str] = "Not allowed"

    def __init__(self) -> None:
        super().__init__("Extracting needs a signed-in account.")


class BusyError(DomainError):
    """Every synchronous slot here is taken, or ai-gateway had no model slot within its short wait. The caller sends
    the document the queued way instead."""

    code: ClassVar[ErrorCode] = ErrorCode.AI_BUSY
    title: ClassVar[str] = "Busy"
    retryable: ClassVar[bool] = True

    def __init__(self) -> None:
        super().__init__("Extraction is busy right now. Send the document as a job instead.")


class AiLimitReachedError(DomainError):
    code: ClassVar[ErrorCode] = ErrorCode.AI_LIMIT_REACHED
    title: ClassVar[str] = "Daily AI allowance used"

    def __init__(self) -> None:
        super().__init__("This account has used its AI calls for today.")


class AiUnavailableError(DomainError):
    """ai-gateway or the model didn't answer (down, timed out, an unusable answer)."""

    code: ClassVar[ErrorCode] = ErrorCode.AI_UNAVAILABLE
    title: ClassVar[str] = "AI unavailable"
    retryable: ClassVar[bool] = True

    def __init__(self) -> None:
        super().__init__("The AI service is not available right now. Try again shortly.")
