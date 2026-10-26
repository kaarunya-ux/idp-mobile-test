"""Service settings, read once from the environment and validated at startup (standard ch. 9).

Every environment variable is listed in README.md (CFG-05). Nested groups use a double underscore:
``LOGGING__LEVEL=DEBUG`` sets ``Settings.logging.level``.
"""

from datetime import timedelta
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, ByteSize, Field, HttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class LoggingSettings(BaseModel):
    """How the service writes its logs (OBS-01): JSON in every environment except local development."""

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    format: Literal["json", "console"] = "json"


class LimitsSettings(BaseModel):
    """Caps that protect the workers from oversized input (IDP-03 ch. 3.5 and 3.7, FIL-05, ML-06)."""

    max_pages: int = Field(1000, ge=1, le=10_000)
    max_image_pixels: int = Field(180_000_000, ge=1_000_000, le=1_000_000_000)
    max_render_side_px: int = Field(5000, ge=500, le=20_000)
    max_document_table_rows: int = Field(20_000, ge=100, le=1_000_000)  # tables written into Word or slides


class OfficeSettings(BaseModel):
    """How LibreOffice is run for Office -> PDF (IDP-03 ch. 3.2 and 3.7)."""

    binary: str = Field("soffice", min_length=1, max_length=255)
    timeout: timedelta = Field(timedelta(seconds=60), ge=timedelta(seconds=5), le=timedelta(minutes=10))


class DatabaseSettings(BaseModel):
    """``conversion_db``: the inbox, the outbox and job item state (build log entry 0, decided 2026-10-22)."""

    url: SecretStr  # postgresql+psycopg://user:password@host:5432/conversion_db
    pool_size: int = Field(5, ge=1, le=50)
    max_overflow: int = Field(2, ge=0, le=50)
    statement_timeout: timedelta = Field(timedelta(seconds=5), ge=timedelta(milliseconds=100))  # DB-02
    lock_timeout: timedelta = Field(timedelta(seconds=3), ge=timedelta(milliseconds=100))
    idle_in_transaction_timeout: timedelta = Field(timedelta(seconds=10), ge=timedelta(seconds=1))


class RabbitSettings(BaseModel):
    """The broker Java and Python exchange messages through (IDP-00 ch. 7.6)."""

    url: SecretStr  # amqp://user:password@host:5672/vhost
    prefetch: int = Field(10, ge=1, le=200)  # MSG-05: intake only records and enqueues, so it can take a few
    delivery_limit: int = Field(5, ge=1, le=20)  # quorum queue x-delivery-limit before the dead-letter queue


class CelerySettings(BaseModel):
    """The worker pools (IDP-03 ch. 3.2, standard ch. 16.2)."""

    broker_url: SecretStr  # amqp://..., the same RabbitMQ on its own virtual host
    soft_time_limit: timedelta = Field(timedelta(minutes=9), ge=timedelta(seconds=30), le=timedelta(hours=1))
    time_limit: timedelta = Field(timedelta(minutes=10), ge=timedelta(seconds=30), le=timedelta(hours=1))
    max_retries: int = Field(3, ge=0, le=10)  # transient failures only (CEL-04)


class StorageSettings(BaseModel):
    """MinIO, run by the Java team; this service reads inputs and writes outputs with its own keys (IDP-00 7.7)."""

    endpoint: HttpUrl
    access_key: SecretStr
    secret_key: SecretStr
    region: str = Field("us-east-1", min_length=1, max_length=32)
    bucket_outputs: str = Field("idp-outputs", min_length=3, max_length=63)
    max_input_size: ByteSize = ByteSize(200 * 1024 * 1024)  # FIL-05: nothing bigger is downloaded
    timeout: timedelta = Field(timedelta(seconds=30), ge=timedelta(seconds=1), le=timedelta(minutes=5))


class OutboxSettings(BaseModel):
    """The relay that publishes outbox rows (OBX-01, Java standard ch. 15.2)."""

    batch_size: int = Field(100, ge=1, le=1000)
    poll_interval: timedelta = Field(timedelta(seconds=1), ge=timedelta(milliseconds=50), le=timedelta(minutes=1))
    lease: timedelta = Field(timedelta(minutes=1), ge=timedelta(seconds=5))  # CLAIMED longer -> PENDING (OBX-03)
    max_attempts: int = Field(10, ge=1, le=100)  # then DEAD + alert (OBX-07)
    backoff_max: timedelta = Field(timedelta(minutes=5), ge=timedelta(seconds=1))


class MissingSettingsError(RuntimeError):
    """A process started without settings it needs, for example a worker without ``DB__URL``."""


class Settings(BaseSettings):
    """All configuration for one process. Built once by ``get_settings()``; invalid values stop the process."""

    model_config = SettingsConfigDict(env_nested_delimiter="__", env_file=".env", extra="ignore")

    service_name: str = Field("conversion-service", min_length=1, max_length=64)
    environment: Literal["local", "dev", "test", "staging", "prod"] = "local"
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    limits: LimitsSettings = Field(default_factory=LimitsSettings)
    office: OfficeSettings = Field(default_factory=OfficeSettings)
    outbox: OutboxSettings = Field(default_factory=OutboxSettings)
    # No defaults: credentials never live in code (CFG-03). Only the processes that use a group require it, so the
    # health server and unit tests run without them; ``require_*`` stops a worker at startup instead.
    db: DatabaseSettings | None = None
    rabbit: RabbitSettings | None = None
    celery: CelerySettings | None = None
    storage: StorageSettings | None = None

    def require_db(self) -> DatabaseSettings:
        return _required(self.db, "DB__URL")

    def require_rabbit(self) -> RabbitSettings:
        return _required(self.rabbit, "RABBIT__URL")

    def require_celery(self) -> CelerySettings:
        return _required(self.celery, "CELERY__BROKER_URL")

    def require_storage(self) -> StorageSettings:
        return _required(self.storage, "STORAGE__ENDPOINT, STORAGE__ACCESS_KEY and STORAGE__SECRET_KEY")


def _required[T](group: T | None, variables: str) -> T:
    if group is None:
        raise MissingSettingsError(f"This process needs {variables}; see README.md")
    return group


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings, reading the environment on the first call only."""
    return Settings()
