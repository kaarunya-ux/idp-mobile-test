"""The clock is timezone-aware and ids sort by creation time (PY-05, DB-03)."""

from idox_conversion.core.clock import SystemClock
from idox_conversion.core.ids import new_id


def test_system_clock_is_timezone_aware() -> None:
    assert SystemClock().now().tzinfo is not None


def test_new_ids_are_unique_and_version_7() -> None:
    first, second = new_id(), new_id()

    assert first != second
    assert first.version == 7
