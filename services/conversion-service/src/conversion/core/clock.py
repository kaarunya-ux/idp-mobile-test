"""The current time behind a protocol, so tests use a fixed clock instead of patching time (TST-05)."""

from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """The current time, timezone-aware in UTC (DB-04)."""
        ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)
