"""The base class for business errors (ERR-02).

Every business exception derives from ``DomainError`` and declares its ``ErrorCode`` and a short title, so the web
handlers (``problem_details.py``) and the message consumers can turn any of them into a response or a ``WorkFailed``
message without knowing the specific type. This module imports no framework, so domain code may use it (DOM-01).
"""

from typing import ClassVar

from ai_gateway.core.error_codes import ErrorCode


class DomainError(Exception):
    """A business rule said no. ``detail`` is safe to show users: no paths, hostnames or stack traces (ERR-06)."""

    code: ClassVar[ErrorCode]
    title: ClassVar[str]
    retryable: ClassVar[bool] = False  # WorkFailed.error.retryable: Java re-dispatches up to 3 times when true

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class UnauthenticatedError(DomainError):
    code: ClassVar[ErrorCode] = ErrorCode.UNAUTHENTICATED
    title: ClassVar[str] = "Not signed in"

    def __init__(self) -> None:
        super().__init__("A valid bearer token is required.")  # never say why: no oracle for attackers


class PermissionDeniedError(DomainError):
    code: ClassVar[ErrorCode] = ErrorCode.FORBIDDEN
    title: ClassVar[str] = "Not allowed"

    def __init__(self, permission: str) -> None:
        super().__init__(f"This needs the {permission} permission.")


class DependencyUnavailableError(DomainError):
    """A service the request needs isn't there (Redis or storage not configured). Reported as 503."""

    code: ClassVar[ErrorCode] = ErrorCode.DEPENDENCY_UNAVAILABLE
    title: ClassVar[str] = "Service unavailable"
    retryable: ClassVar[bool] = True

    def __init__(self, what: str) -> None:
        super().__init__(f"The {what} is not available right now.")
