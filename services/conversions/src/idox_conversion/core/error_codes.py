"""Error codes this service reports, in the shared format ``<AREA>-<HTTP>-<TOPIC>-<NNN>`` (ERR-05, IDP-00 ch. 7.4).

The codes are the ones in IDP-03 ch. 3.8. Never reuse or change a released code; only add new ones, as they are needed.
Worker errors reach users through the job item's ``error.code`` in a ``WorkFailed`` message (IDP-00 ch. 7.4).
"""

from enum import StrEnum


class ErrorCode(StrEnum):
    """Every error code the conversion service can report."""

    ROUTE_UNSUPPORTED = "CNV-422-ROUTE-001"  # IDP-03 ch. 3.8: no route from this source to this target
    PDF_ENCRYPTED = "CNV-422-PDF-001"  # IDP-03 ch. 3.7/3.8: an encrypted PDF and no password was given
    PDF_CORRUPT = "CNV-422-PDF-002"  # IDP-03 ch. 3.8
    PDF_UNLOCK_REFUSED = "CNV-422-PDF-003"  # IDP-03 ch. 3.8: the password given does not open the PDF
    PLAN_INVALID = "CNV-422-PLAN-001"  # IDP-03 ch. 3.8: an invalid edit plan
    LIMIT_EXCEEDED = "CNV-422-LIMIT-001"  # IDP-03 ch. 3.8: too many pages, sheets or operations
    TIMEOUT = "CNV-504-TIMEOUT-001"  # IDP-03 ch. 3.8: a converter or LibreOffice passed its time limit
    MODEL_UNAVAILABLE = "CNV-503-MODEL-001"  # new, not in IDP-03 ch. 3.8: the model server did not answer (LLM-05)
    MODEL_ANSWER_INVALID = "CNV-502-MODEL-002"  # new, not in IDP-03 ch. 3.8: an answer that is not valid (LLM-03)
    INTERNAL_ERROR = "CNV-500-INTERNAL-001"  # new, not in IDP-03 ch. 3.8: an unexpected failure (ERR-06)

    @property
    def http_status(self) -> int:
        """The HTTP status embedded in the code: ``CNV-422-ROUTE-001`` → 422."""
        return int(self.value.split("-")[1])
