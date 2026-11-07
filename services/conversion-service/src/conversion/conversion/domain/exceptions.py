"""Errors raised by the conversion rules (ERR-02, IDP-03 ch. 3.8)."""

from conversion.conversion.domain.formats import Format
from conversion.core.error_codes import ErrorCode
from conversion.core.errors import DomainError


class RouteNotFoundError(DomainError):
    """No route turns ``source`` into ``target``. Reported to the user as ``CNV-422-ROUTE-001``."""

    code = ErrorCode.ROUTE_NOT_SUPPORTED
    title = "Conversion not supported"

    def __init__(self, source: Format, target: Format) -> None:
        super().__init__(f"There is no conversion from {source} to {target}.")
        self.source = source
        self.target = target


class InvalidRouteError(ValueError):
    """A route whose steps don't chain together. A bug in the route table, never a user error; tests catch it."""


class InvalidDocumentModelError(ValueError):
    """A reader built an impossible document model (a ragged table, empty text). A bug, never a user error."""


class InvalidOptionError(DomainError):
    """A conversion option is outside its allowed values. Reported as ``CNV-422-OPTIONS-001``."""

    code = ErrorCode.INVALID_OPTION
    title = "Invalid conversion option"

    def __init__(self, option: str, problem: str) -> None:
        super().__init__(f"Option {option} {problem}.")
        self.option = option


class PageRangeOutsideDocumentError(InvalidOptionError):
    """The page range names no page that exists in this document."""

    def __init__(self, page_count: int) -> None:
        super().__init__("pageRange", f"names no page of this {page_count}-page document")


class UnreadableImageError(DomainError):
    """The image is damaged or in a format Pillow can't read. Reported as ``CNV-422-FILE-001``."""

    code = ErrorCode.FILE_UNREADABLE
    title = "File can't be read"

    def __init__(self) -> None:
        super().__init__("The image is damaged or not in a supported format.")


class CorruptPdfError(DomainError):
    """The PDF can't be opened. Reported as ``CNV-422-PDF-002``."""

    code = ErrorCode.PDF_CORRUPT
    title = "PDF can't be read"

    def __init__(self) -> None:
        super().__init__("The PDF is damaged and can't be opened.")


class PasswordRequiredError(DomainError):
    """The PDF is encrypted and no password was given. Reported as ``CNV-422-PDF-001``."""

    code = ErrorCode.PDF_ENCRYPTED
    title = "PDF is password protected"

    def __init__(self) -> None:
        super().__init__("The PDF is protected by a password.")


class LimitExceededError(DomainError):
    """The input is bigger than the service accepts (pages, pixels, page size). Reported as ``CNV-422-LIMIT-001``."""

    code = ErrorCode.LIMIT_EXCEEDED
    title = "File too large"


class UnreadableDocumentError(DomainError):
    """An Office document or CSV is damaged or not what it claims to be. Reported as ``CNV-422-FILE-001``."""

    code = ErrorCode.FILE_UNREADABLE
    title = "File can't be read"

    def __init__(self) -> None:
        super().__init__("The document is damaged or not in a supported format.")


class SheetNotFoundError(InvalidOptionError):
    """The ``sheet`` option names a sheet the workbook doesn't have."""

    def __init__(self, sheet: str) -> None:
        super().__init__("sheet", f"names '{sheet}', which is not a sheet in this workbook")


class ConversionTimeoutError(DomainError):
    """A converter ran past its time limit and was stopped. Reported as ``CNV-504-TIMEOUT-001``."""

    code = ErrorCode.CONVERSION_TIMEOUT
    title = "Conversion took too long"
    retryable = True  # the machine may only have been busy

    def __init__(self, seconds: float) -> None:
        super().__init__(f"The conversion took longer than {seconds:g} seconds and was stopped.")


class NoTableFoundError(DomainError):
    """A CSV export was asked of a document with no table. Reported as ``CNV-422-TABLE-001``."""

    code = ErrorCode.NO_TABLE
    title = "No table found"

    def __init__(self) -> None:
        super().__init__("The document has no table to export.")


class InvalidRequestError(DomainError):
    """A ``ConversionRequested`` this service can't act on: an unknown job type, input type or target, or several
    inputs where only one is allowed. Reported as ``CNV-422-REQUEST-001``."""

    code = ErrorCode.INVALID_REQUEST
    title = "Request can't be processed"


class OcrNotAvailableError(DomainError):
    """The route needs OCR (a scanned PDF to Word, Excel or CSV), which this build doesn't have yet (decision D-1).
    Reported as ``CNV-422-ROUTE-001``."""

    code = ErrorCode.ROUTE_NOT_SUPPORTED
    title = "Conversion not supported"

    def __init__(self) -> None:
        super().__init__("This document is scanned; converting it needs text recognition, which isn't available yet.")


class UnexpectedFailureError(DomainError):
    """Something outside the user's file went wrong (a bug, or a dependency down after every retry). Reported as
    ``CNV-500-INTERNAL-001``; Java may try again."""

    code = ErrorCode.INTERNAL_ERROR
    title = "Conversion failed"
    retryable = True

    def __init__(self) -> None:
        super().__init__("The conversion failed for a reason unrelated to the file. Please try again.")


class WrongPasswordError(DomainError):
    """The edit plan's password doesn't open the PDF. Reported as ``CNV-422-PDF-003``."""

    code = ErrorCode.PDF_WRONG_PASSWORD
    title = "Wrong PDF password"

    def __init__(self) -> None:
        super().__init__("The password doesn't open this PDF.")


class InvalidPlanError(DomainError):
    """An edit plan that is malformed or can't be applied to this PDF. Reported as ``CNV-422-PLAN-001``."""

    code = ErrorCode.INVALID_PLAN
    title = "Edit plan can't be applied"


class AiUnavailableError(DomainError):
    """A scanned route needs the model, and ai-gateway didn't answer (busy, model down, a timeout). Reported as
    ``CNV-503-AI-001``; Java may try again later."""

    code = ErrorCode.AI_UNAVAILABLE
    title = "Text recognition unavailable"
    retryable = True

    def __init__(self) -> None:
        super().__init__("The scanned document couldn't be read right now. Please try again later.")


class AiLimitReachedError(DomainError):
    """The account has used its AI calls for today (ai-gateway's daily cap). Reported as ``CNV-429-AI-001``."""

    code = ErrorCode.AI_LIMIT_REACHED
    title = "Daily limit reached"

    def __init__(self) -> None:
        super().__init__("Converting scanned documents is limited per day; this account has reached today's limit.")


class FileTooLargeError(DomainError):
    """Over the synchronous route's size or pages (ADR 0002). Reported as ``CNV-413-FILE-001``: send it as a job."""

    code = ErrorCode.FILE_TOO_LARGE
    title = "Too large to convert while waiting"


class FileTypeUnsupportedError(DomainError):
    """The content is none of the formats this service reads (FIL-03). Reported as ``CNV-415-FILE-001``."""

    code = ErrorCode.FILE_TYPE_UNSUPPORTED
    title = "File type not supported"

    def __init__(self) -> None:
        super().__init__("The file is not a PDF, Word, Excel, PowerPoint, CSV, PNG or JPEG file.")


class BusyError(DomainError):
    """Every synchronous slot is taken (ADR 0002). Reported as ``CNV-429-BUSY-001``: send it as a job."""

    code = ErrorCode.BUSY
    title = "Busy"
    retryable = True

    def __init__(self) -> None:
        super().__init__("Conversion is busy right now. Send the file as a job instead.")


class NeedsReadingError(DomainError):
    """The synchronous route doesn't read a scanned PDF (ADR 0002), nor a photo when this build has no reader: their
    text has to be read. Reported as ``CNV-422-SYNC-001``: send it as a job."""

    code = ErrorCode.NEEDS_READING
    title = "Needs a job"

    def __init__(self) -> None:
        super().__init__("This is a scan or a picture, so its text has to be read. Send it as a job instead.")


class NoAccountError(DomainError):
    """A photo's reading is counted on an account (ai-gateway's daily limit), and the token has none: platform staff,
    or a user still onboarding. Reported as ``CNV-403-AUTH-001``."""

    code = ErrorCode.FORBIDDEN
    title = "Not allowed"

    def __init__(self) -> None:
        super().__init__("Reading a picture's text needs a signed-in account.")
