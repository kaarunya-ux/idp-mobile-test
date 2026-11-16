"""The base class for business errors (ERR-02).

Every business exception derives from ``DomainError`` and declares its ``ErrorCode``, a short title and whether
trying again could help, so any of them becomes a ``WorkFailed`` message without knowing the specific type.
"""

from typing import Any, ClassVar

from conversion.core.error_codes import ErrorCode


class DomainError(Exception):
    """A business rule said no. ``detail`` is safe to show users: no paths, hostnames or stack traces (ERR-06)."""

    code: ClassVar[ErrorCode]
    title: ClassVar[str]
    retryable: ClassVar[bool] = False  # WorkFailed.error.retryable: Java re-dispatches up to 3 times when true

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail

    def __reduce__(self) -> tuple[Any, ...]:
        """Rebuilt from its detail and attributes whatever its ``__init__`` takes: the synchronous route's errors
        come back from a worker process, and an exception that can't be rebuilt there breaks the whole pool."""
        return _rebuilt, (type(self), self.detail, self.__dict__)


def _rebuilt[E: DomainError](error_type: type[E], detail: str, attributes: dict[str, Any]) -> E:
    error = error_type.__new__(error_type)
    Exception.__init__(error, detail)
    error.__dict__.update(attributes)
    return error


class UnauthenticatedError(DomainError):
    code = ErrorCode.UNAUTHENTICATED
    title = "Not signed in"

    def __init__(self) -> None:
        super().__init__("A valid bearer token is required.")  # never say why: no oracle for attackers


class PermissionDeniedError(DomainError):
    code = ErrorCode.FORBIDDEN
    title = "Not allowed"

    def __init__(self, permission: str) -> None:
        super().__init__(f"This needs the {permission} permission.")
