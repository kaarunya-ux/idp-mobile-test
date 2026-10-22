"""Identifiers: UUIDv7, which sort by creation time (DB-03, standard ch. 3.4)."""

from uuid import UUID

import uuid_utils


def new_id() -> UUID:
    """A new UUIDv7 as a standard-library ``UUID``."""
    return UUID(bytes=uuid_utils.uuid7().bytes)
