"""Error codes this service reports, in the shared format ``<AREA>-<HTTP>-<TOPIC>-<NNN>`` (ERR-05, IDP-00 ch. 7.4).

Worker errors reach users through the job item's ``error.code`` in a ``WorkFailed`` message, not as HTTP
responses (IDP-00 ch. 7.4). Never reuse or change a released code; only add new ones, as they are needed.
The full list this service will use is in IDP-03 ch. 2.12.
"""

from enum import StrEnum


class ErrorCode(StrEnum):
    """Every error code extraction-service can report."""

    REQUEST_INVALID = (
        "EXT-400-VALIDATION-001"  # new, not in IDP-03 ch. 2.12: the request shape is wrong (RTR/SCH rules)
    )
    FILE_REJECTED = "EXT-422-FILE-001"  # IDP-03 ch. 2.12: the file is not a supported, readable document
    PAGES_LIMIT_EXCEEDED = "EXT-422-PAGES-001"  # too many pages
    QUALITY_BLURRED = "EXT-422-QUALITY-001"
    QUALITY_TOO_DARK = "EXT-422-QUALITY-002"
    QUALITY_GLARE = "EXT-422-QUALITY-003"  # also used for "too bright": IDP-03 names no code for it
    QUALITY_RESOLUTION = "EXT-422-QUALITY-004"
    QUALITY_EDGES_NOT_FOUND = "EXT-422-QUALITY-005"
    DEFINITION_INVALID = "EXT-422-DEFINITION-001"  # IDP-03 ch. 2.12: a template definition is not valid
    UNAUTHENTICATED = "EXT-401-AUTH-001"  # new: no valid bearer token (SEC-01)
    FORBIDDEN = "EXT-403-AUTH-001"  # new: the token lacks the permission (SEC-03)
    TEMPLATE_NOT_FOUND = "EXT-404-TEMPLATE-001"  # IDP-03 ch. 2.12; also another account's template (SEC-05)
    DOCUMENT_TYPE_NOT_FOUND = "EXT-404-DOCTYPE-001"  # new
    TEMPLATE_NOT_DRAFT = "EXT-409-TEMPLATE-001"  # IDP-03 ch. 2.12: only a DRAFT version can change
    TEMPLATE_NOT_TESTED = "EXT-409-TEMPLATE-002"  # IDP-03 ch. 2.12: publish without a passing test (or override)
    TEMPLATE_STATE = "EXT-409-TEMPLATE-003"  # new: the version is not in the state the action needs
    DOCUMENT_TYPE_EXISTS = "EXT-409-DOCTYPE-001"  # new
    IDEMPOTENCY_KEY_REUSED = "EXT-409-IDEMPOTENCY-001"  # new: the same Idempotency-Key with a different body
    VERSION_MISMATCH = "EXT-412-VERSION-001"  # new: If-Match doesn't match the current version (optimistic lock)
    INTERNAL_ERROR = "EXT-500-INTERNAL-001"  # new, not in IDP-03 ch. 2.12: an unexpected failure (ERR-06)
    DEPENDENCY_UNAVAILABLE = "EXT-503-DEPENDENCY-001"  # new: the database isn't configured or reachable
    TIMEOUT = "EXT-504-PIPELINE-001"  # IDP-03 ch. 2.12: the item ran past its time limit

    @property
    def http_status(self) -> int:
        """The HTTP status embedded in the code: ``EXT-400-VALIDATION-001`` → 400."""
        return int(self.value.split("-")[1])
