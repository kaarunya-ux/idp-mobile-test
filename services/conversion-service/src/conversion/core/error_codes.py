"""Error codes this service reports, in the shared format ``<AREA>-<HTTP>-<TOPIC>-<NNN>`` (ERR-05, IDP-00 ch. 7.4).

Worker errors reach users through the job item's ``error.code`` in a ``WorkFailed`` message, not as HTTP
responses (IDP-00 ch. 7.4). Never reuse or change a released code; only add new ones, as they are needed.
The full list this service will use is in IDP-03 ch. 3.8.
"""

from enum import StrEnum


class ErrorCode(StrEnum):
    """Every error code conversion-service can report."""

    ROUTE_NOT_SUPPORTED = "CNV-422-ROUTE-001"
    PDF_ENCRYPTED = "CNV-422-PDF-001"  # a password is needed to open it
    PDF_CORRUPT = "CNV-422-PDF-002"
    LIMIT_EXCEEDED = "CNV-422-LIMIT-001"
    FILE_UNREADABLE = "CNV-422-FILE-001"  # new, not in IDP-03 ch. 3.8: a damaged or unsupported file
    INVALID_OPTION = "CNV-422-OPTIONS-001"  # new, not in IDP-03 ch. 3.8: an option outside its allowed values
    CONVERSION_TIMEOUT = "CNV-504-TIMEOUT-001"
    NO_TABLE = "CNV-422-TABLE-001"  # new, not in IDP-03 ch. 3.8: a table export from a document without one

    @property
    def http_status(self) -> int:
        """The HTTP status embedded in the code: ``CNV-422-ROUTE-001`` → 422."""
        return int(self.value.split("-")[1])
