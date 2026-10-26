"""New identifiers (DB-03): UUIDv7, which sort by creation time and so keep index inserts cheap."""

from uuid import UUID

import uuid_utils


def new_id() -> UUID:
    return UUID(bytes=uuid_utils.uuid7().bytes)
