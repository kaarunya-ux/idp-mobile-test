"""The base class for business errors (ERR-02).

Every business exception derives from ``DomainError`` and declares its ``ErrorCode``, a short title and whether
trying again could help, so any of them becomes a ``WorkFailed`` message without knowing the specific type.
"""

from typing import ClassVar

from conversion.core.error_codes import ErrorCode


class DomainError(Exception):
    """A business rule said no. ``detail`` is safe to show users: no paths, hostnames or stack traces (ERR-06)."""

    code: ClassVar[ErrorCode]
    title: ClassVar[str]
    retryable: ClassVar[bool] = False  # WorkFailed.error.retryable: Java re-dispatches up to 3 times when true

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail
