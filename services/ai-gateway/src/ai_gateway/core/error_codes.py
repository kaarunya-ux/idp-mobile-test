"""Error codes this service reports, in the shared format ``<AREA>-<HTTP>-<TOPIC>-<NNN>`` (ERR-05, IDP-00 ch. 7.4).

IDP-03 ch. 4.6 lists the gateway's codes; the ones marked "new" are added for the standard's request and auth rules.
Never reuse or change a released code; only add new ones.
"""

from enum import StrEnum


class ErrorCode(StrEnum):
    REQUEST_INVALID = "AIG-400-VALIDATION-001"  # new: the request shape is wrong
    UNAUTHENTICATED = "AIG-401-AUTH-001"  # new: no valid service token
    FORBIDDEN = "AIG-403-AUTH-001"  # new: a client that may not call the gateway
    INPUT_INVALID = "AIG-422-INPUT-001"  # IDP-03: image too large or unsupported, or missing in storage
    CAP_REACHED = "AIG-429-CAP-001"  # IDP-03: the account's daily AI calls are used up
    INTERNAL_ERROR = "AIG-500-INTERNAL-001"  # new: an unexpected failure (ERR-06)
    OUTPUT_INVALID = "AIG-502-OUTPUT-001"  # IDP-03: the model's answer isn't the schema, even after a repair attempt
    MODEL_UNAVAILABLE = "AIG-503-MODEL-001"  # IDP-03: the model server is failing (breaker open) or unreachable
    BUSY = "AIG-503-BUSY-001"  # IDP-03: no model slot within the wait timeout
    DEPENDENCY_UNAVAILABLE = "AIG-503-DEPENDENCY-001"  # new: Redis or storage isn't configured or reachable

    @property
    def http_status(self) -> int:
        return int(self.value.split("-")[1])
