"""Alembic environment: migrates ``extraction_db`` with the URL from ``Settings`` (CFG-01)."""

from alembic import context
from sqlalchemy import create_engine

# registers inbox_message and outbox_event
from extraction.core import inbox, outbox  # noqa: F401
from extraction.core.config import get_settings
from extraction.core.db import Base

# registers extraction_item
from extraction.pipeline.infrastructure.persistence import orm  # noqa: F401
from extraction.template.infrastructure import orm as template_orm  # noqa: F401

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
