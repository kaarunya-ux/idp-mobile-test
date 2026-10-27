"""Alembic environment: migrates ``conversion_db`` with the URL from ``Settings`` (CFG-01)."""

from alembic import context
from sqlalchemy import create_engine

# registers conversion_item
from conversion.conversion.infrastructure.persistence import orm  # noqa: F401

# registers inbox_message and outbox_event
from conversion.core import inbox, outbox  # noqa: F401
from conversion.core.config import get_settings
from conversion.core.db import Base

target_metadata = Base.metadata


def run_migrations() -> None:
    url = context.config.attributes.get("url") or get_settings().require_db().url.get_secret_value()
    engine = create_engine(url)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


run_migrations()
