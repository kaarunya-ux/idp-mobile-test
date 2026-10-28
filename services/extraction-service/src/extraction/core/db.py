"""The database connection: one engine per process, short sessions, and server-side timeouts (standard ch. 8).

Every process here is synchronous end to end (Celery workers, and the consumer and relay run their database work
in a worker thread), so one sync engine on psycopg 3 serves all of them (ASY-01).
"""

from datetime import timedelta

from sqlalchemy import Engine, MetaData, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from extraction.core.config import DatabaseSettings

# Constraint names from standard ch. 4.5, so migrations name them the same way every time (DB-07).
_NAMING = {
    "ix": "idx_%(table_name)s__%(column_0_N_name)s",
    "uq": "uq_%(table_name)s__%(column_0_N_name)s",
    "ck": "ck_%(table_name)s__%(constraint_name)s",
    "fk": "fk_%(table_name)s__%(column_0_N_name)s__%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=_NAMING)


type SessionFactory = sessionmaker[Session]


def create_db_engine(settings: DatabaseSettings, *, application_name: str) -> Engine:
    """The process's engine, with statement, lock and idle-transaction timeouts on every connection (DB-02)."""
    timeouts = {
        "statement_timeout": settings.statement_timeout,
        "lock_timeout": settings.lock_timeout,
        "idle_in_transaction_session_timeout": settings.idle_in_transaction_timeout,
    }
    options = " ".join(f"-c {name}={_milliseconds(value)}" for name, value in timeouts.items())
    return create_engine(
        settings.url.get_secret_value(),
        pool_size=settings.pool_size,
        max_overflow=settings.max_overflow,
        pool_pre_ping=True,
        pool_recycle=1800,
        connect_args={"options": options, "application_name": application_name},
    )


def create_session_factory(engine: Engine) -> SessionFactory:
    return sessionmaker(engine, expire_on_commit=False)  # DAO-04


def _milliseconds(value: timedelta) -> int:
    return int(value.total_seconds() * 1000)
