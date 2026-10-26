"""The clock is injected, never read from a global (PY-05, DOM-05, TST-05)."""

from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    """Where code gets "now" from, so tests can pin it."""

    def now(self) -> datetime:
        """The current time as a timezone-aware UTC datetime."""
        ...


class SystemClock:
    """The real clock."""

    def now(self) -> datetime:
        return datetime.now(UTC)
