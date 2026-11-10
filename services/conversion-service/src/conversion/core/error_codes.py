"""Error codes this service reports, in the shared format ``<AREA>-<HTTP>-<TOPIC>-<NNN>`` (ERR-05, IDP-00 ch. 7.4).

Worker errors reach users through the job item's ``error.code`` in a ``WorkFailed`` message; on the synchronous route
(ADR 0002) the same codes are HTTP problem details. Never reuse or change a released code; only add new ones, as they
are needed.
The full list this service will use is in IDP-03 ch. 3.8.
"""

from enum import StrEnum


class ErrorCode(StrEnum):
    """Every error code conversion-service can report."""

    ROUTE_NOT_SUPPORTED = "CNV-422-ROUTE-001"
    PDF_ENCRYPTED = "CNV-422-PDF-001"  # a password is needed to open it
    PDF_CORRUPT = "CNV-422-PDF-002"
    # an error code, not a password
    PDF_WRONG_PASSWORD = "CNV-422-PDF-003"  # noqa: S105
    INVALID_PLAN = "CNV-422-PLAN-001"  # an edit plan that can't be applied
    LIMIT_EXCEEDED = "CNV-422-LIMIT-001"
    FILE_UNREADABLE = "CNV-422-FILE-001"  # new, not in IDP-03 ch. 3.8: a damaged or unsupported file
    INVALID_OPTION = "CNV-422-OPTIONS-001"  # new, not in IDP-03 ch. 3.8: an option outside its allowed values
    CONVERSION_TIMEOUT = "CNV-504-TIMEOUT-001"
    NO_TABLE = "CNV-422-TABLE-001"  # new, not in IDP-03 ch. 3.8: a table export from a document without one
    INVALID_REQUEST = "CNV-422-REQUEST-001"  # new: a ConversionRequested this service can't act on (type, inputs)
    INTERNAL_ERROR = "CNV-500-INTERNAL-001"  # new: an unexpected failure or a dependency down; retryable
    AI_UNAVAILABLE = (
        "CNV-503-AI-001"  # new: a scanned route got no answer from ai-gateway (busy, model down); retryable
    )
    AI_LIMIT_REACHED = "CNV-429-AI-001"  # new: the account has used its AI calls for today (ai-gateway's cap)
    # new, POST /api/v1/convert (ADR 0002)
    REQUEST_INVALID = "CNV-400-REQUEST-001"  # a missing or malformed form field
    UNAUTHENTICATED = "CNV-401-AUTH-001"  # no valid bearer token (SEC-01)
    FORBIDDEN = "CNV-403-AUTH-001"  # the token lacks the permission or an account (SEC-03)
    FILE_TOO_LARGE = "CNV-413-FILE-001"  # over the synchronous route's size or pages: send it as a job
    FILE_TYPE_UNSUPPORTED = "CNV-415-FILE-001"  # the content is none of the formats this service reads
    BUSY = "CNV-429-BUSY-001"  # every synchronous slot is taken: send it as a job
    NEEDS_READING = "CNV-422-SYNC-001"  # a scan over SYNC__MAX_READ_PAGES, or no reader in this build: send it as a job

    @property
    def http_status(self) -> int:
        """The HTTP status embedded in the code: ``CNV-422-ROUTE-001`` → 422."""
        return int(self.value.split("-")[1])
