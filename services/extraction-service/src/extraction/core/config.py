"""Service settings, read once from the environment and validated at startup (standard ch. 9).

Every environment variable is listed in README.md (CFG-05). Nested groups use a double underscore:
``LOGGING__LEVEL=DEBUG`` sets ``Settings.logging.level``.
"""

from datetime import timedelta
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ByteSize, Field, HttpUrl, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class LoggingSettings(BaseModel):
    """How the service writes its logs (OBS-01): JSON in every environment except local development."""

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    format: Literal["json", "console"] = "json"


class OcrSettings(BaseModel):
    """Where the OCR models are and how they run (ML-01, ML-06, ML-10). Every model is loaded on the CPU."""

    models_dir: Path = Path("models")
    models_file: Path = Path("models.yaml")
    threads: int = Field(4, ge=1, le=64)
    inference_timeout: timedelta = Field(timedelta(seconds=60), ge=timedelta(seconds=1), le=timedelta(minutes=10))
    max_pixels: int = Field(60_000_000, ge=1_000_000, le=1_000_000_000)


class QualitySettings(BaseModel):
    """Page quality gate thresholds (IDP-03 ch. 2.7). Changing any of them needs an evaluation report (ML-07, ML-12).

    ``saturation_level``, ``glare_soft_edge_gradient`` and ``require_document_edges`` are not in IDP-03: they make the
    glare and edge checks usable on white documents and on already-cropped scans (see the README). IDP-03 rejects a page
    whose edges are not found (stage 2) yet continues when the warp fails (stage 3); on 19 test images 15 show no
    detectable edges, so the rejection is opt-in.
    """

    blur_min: float = Field(100.0, gt=0)  # variance of the Laplacian below this is blurred
    brightness_range: tuple[float, float] = (0.25, 0.90)  # mean luminance outside this is too dark / too bright
    glare_max: float = Field(0.04, ge=0, le=1)  # share of the page covered by glare
    min_short_side_px: int = Field(900, ge=1)  # a shorter document side is too low resolution
    saturation_level: int = Field(250, ge=200, le=255)  # grey level from which a pixel counts as saturated
    glare_soft_edge_gradient: float = Field(15.0, gt=0)  # grey levels per pixel: softer-edged saturated blobs are glare
    require_document_edges: bool = False  # refuse a page whose edges cannot be found (see the README: off by default)

    @model_validator(mode="after")
    def _brightness_range_is_ordered(self) -> "QualitySettings":
        low, high = self.brightness_range
        if not 0 <= low < high <= 1:
            message = "brightness_range must be two fractions with 0 <= low < high <= 1."
            raise ValueError(message)
        return self


class LimitsSettings(BaseModel):
    """Caps that protect the workers from oversized input (IDP-03 ch. 2.6 stages 1-2, FIL-05, ML-06).

    Plan limits are enforced upstream (IDP-00 ch. 7.9); these are the hard ceilings of one deployment.
    """

    max_file_size: ByteSize = ByteSize(200 * 1024 * 1024)
    max_pages: int = Field(200, ge=1, le=10_000)
    max_image_pixels: int = Field(180_000_000, ge=1_000_000, le=1_000_000_000)
    pdf_dpi: int = Field(300, ge=72, le=600)


class PrepareSettings(BaseModel):
    """How pages are straightened and cleaned before OCR (IDP-03 ch. 2.6 stage 3)."""

    orientation_min_confidence: float = Field(0.6, gt=0, le=1)  # below this the page is not rotated
    min_card_area_fraction: float = Field(0.15, gt=0, lt=1)  # a card must cover at least this much of the photo


class VerifySettings(BaseModel):
    """When two engines are taken to agree (IDP-03 ch. 2.7). Changing a number needs an evaluation report (ML-07)."""

    name_similarity: float = Field(0.95, gt=0, le=1)  # default threshold for SIMILARITY fields


class ReviewSettings(BaseModel):
    """When an accepted field still goes to a person (IDP-03 ch. 2.7)."""

    confidence_threshold: float = Field(0.85, ge=0, le=1)  # below this a field needs review even if accepted


class DatabaseSettings(BaseModel):
    """``extraction_db`` (IDP-03 ch. 2.4): the inbox, the outbox and job item state; templates come later."""

    url: SecretStr  # postgresql+psycopg://user:password@host:5432/extraction_db
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
    """The pipeline workers (IDP-03 ch. 2.2, standard ch. 16.2)."""

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
    bucket_derived: str = Field("idp-derived", min_length=3, max_length=63)  # page images and result.json (IDP-00 7.7)
    max_input_size: ByteSize = ByteSize(200 * 1024 * 1024)  # FIL-05: nothing bigger is downloaded
    timeout: timedelta = Field(timedelta(seconds=30), ge=timedelta(seconds=1), le=timedelta(minutes=5))


class OutboxSettings(BaseModel):
    """The relay that publishes outbox rows (OBX-01, Java standard ch. 15.2)."""

    batch_size: int = Field(100, ge=1, le=1000)
    poll_interval: timedelta = Field(timedelta(seconds=1), ge=timedelta(milliseconds=50), le=timedelta(minutes=1))
    lease: timedelta = Field(timedelta(minutes=1), ge=timedelta(seconds=5))  # CLAIMED longer -> PENDING (OBX-03)
    max_attempts: int = Field(10, ge=1, le=100)  # then DEAD + alert (OBX-07)
    backoff_max: timedelta = Field(timedelta(minutes=5), ge=timedelta(seconds=1))


class AiFallbackSettings(BaseModel):
    """The AI fallback for fields the OCR engines can't settle (IDP-03 ch. 2.6 stage 7).

    Until ai-gateway exists, this calls the model host directly (an OpenAI-style server such as llama-server), as the
    web extraction app did; then ``url`` points at ai-gateway instead. Leave ``url`` unset to turn the fallback off:
    unsettled fields stay NEEDS_REVIEW, which is always safe.
    """

    url: HttpUrl | None = None  # e.g. http://10.0.3.66:8081 (the model host)
    api_key: SecretStr | None = None
    model: str | None = Field(None, max_length=128)  # sent with requests; llama-server ignores it
    timeout: timedelta = Field(timedelta(seconds=120), ge=timedelta(seconds=1), le=timedelta(minutes=10))
    max_edge_px: int = Field(1280, ge=256, le=4096)  # longest image side sent to the model
    confidence: float = Field(0.9, gt=0, le=1)  # given to an AI value that passes the field's rule (ML-07)


class MissingSettingsError(RuntimeError):
    """A process started without settings it needs, for example a worker without ``DB__URL``."""


class Settings(BaseSettings):
    """All configuration for one process. Built once by ``get_settings()``; invalid values stop the process."""

    model_config = SettingsConfigDict(env_nested_delimiter="__", env_file=".env", extra="ignore")

    service_name: str = Field("extraction-service", min_length=1, max_length=64)
    environment: Literal["local", "dev", "test", "staging", "prod"] = "local"
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    ocr: OcrSettings = Field(default_factory=OcrSettings)
    quality: QualitySettings = Field(default_factory=QualitySettings)
    limits: LimitsSettings = Field(default_factory=LimitsSettings)
    prepare: PrepareSettings = Field(default_factory=PrepareSettings)
    verify: VerifySettings = Field(default_factory=VerifySettings)
    review: ReviewSettings = Field(default_factory=ReviewSettings)
    outbox: OutboxSettings = Field(default_factory=OutboxSettings)
    ai_fallback: AiFallbackSettings = Field(default_factory=AiFallbackSettings)
    # Needed only by the processes that use them (consumers, workers, relay); read through require_*().
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
