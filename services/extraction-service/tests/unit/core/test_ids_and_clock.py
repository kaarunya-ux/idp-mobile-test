"""Ids are UUIDv7 and time comes from an injectable clock (DB-03, PY-05, DOM-05)."""

from datetime import timedelta

from extraction.core.clock import SystemClock
from extraction.core.ids import new_id


def test_makes_version_7_ids_when_asked() -> None:
    assert new_id().version == 7


def test_makes_ids_that_sort_by_creation_time_when_made_in_order() -> None:
    ids = [new_id() for _ in range(50)]

    assert ids == sorted(ids)


def test_reads_timezone_aware_utc_time_when_asked() -> None:
    now = SystemClock().now()

    assert now.utcoffset() == timedelta(0)
