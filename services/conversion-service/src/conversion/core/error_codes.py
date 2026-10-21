"""Error codes this service reports, in the shared format ``<AREA>-<HTTP>-<TOPIC>-<NNN>`` (ERR-05, IDP-00 ch. 7.4).

Worker errors reach users through the job item's ``error.code`` in a ``WorkFailed`` message, not as HTTP
responses (IDP-00 ch. 7.4). Never reuse or change a released code; only add new ones, as they are needed.
The full list this service will use is in IDP-03 ch. 3.8.
"""

from enum import StrEnum


class ErrorCode(StrEnum):
    """Every error code conversion-service can report."""

    ROUTE_NOT_SUPPORTED = "CNV-422-ROUTE-001"

    @property
    def http_status(self) -> int:
        """The HTTP status embedded in the code: ``CNV-422-ROUTE-001`` → 422."""
        return int(self.value.split("-")[1])
