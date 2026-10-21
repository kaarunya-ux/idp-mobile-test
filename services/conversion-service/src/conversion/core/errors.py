"""The base class for business errors (ERR-02).

Every business exception derives from ``DomainError`` and declares its ``ErrorCode`` and a short title, so the
message consumer (step 4) can turn any of them into a ``WorkFailed`` message without knowing the specific type.
"""

from typing import ClassVar

from conversion.core.error_codes import ErrorCode


class DomainError(Exception):
    """A business rule said no. ``detail`` is safe to show users: no paths, hostnames or stack traces (ERR-06)."""

    code: ClassVar[ErrorCode]
    title: ClassVar[str]

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail
