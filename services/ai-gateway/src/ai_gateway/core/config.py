"""Service settings, read once from the environment and validated at startup (standard ch. 9).

Every variable is listed in README.md (CFG-05). Nested groups use a double underscore: ``MODEL__BASE_URL``.
"""

from datetime import timedelta
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, ByteSize, Field, HttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class LoggingSettings(BaseModel):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    format: Literal["json", "console"] = "json"


class ModelTimeouts(BaseModel):
    """IDP-03 ch. 4.5: per task."""

    field_read: timedelta = Field(timedelta(seconds=30), ge=timedelta(seconds=1), le=timedelta(minutes=5))
    classify: timedelta = Field(timedelta(seconds=30), ge=timedelta(seconds=1), le=timedelta(minutes=5))
    page_analysis: timedelta = Field(timedelta(seconds=90), ge=timedelta(seconds=1), le=timedelta(minutes=10))
    template_suggest: timedelta = Field(timedelta(seconds=90), ge=timedelta(seconds=1), le=timedelta(minutes=10))
    connect: timedelta = Field(timedelta(seconds=2), ge=timedelta(milliseconds=100), le=timedelta(seconds=30))


class ModelSettings(BaseModel):
    """The local model server (OpenAI-style ``/v1``; IDP-03 ch. 4.1)."""

    base_url: HttpUrl = HttpUrl("http://10.0.3.66:4000/v1")
    api_key: SecretStr | None = None
    vl_model: str = Field("vl", min_length=1, max_length=128)  # vision tasks
    smart_model: str = Field("smart", min_length=1, max_length=128)  # text only
    auto_model: str = Field("auto", min_length=1, max_length=128)  # open-ended questions
    vl_max_concurrent: int = Field(4, ge=1, le=256)  # the server's capacity, shared by every gateway replica
    timeouts: ModelTimeouts = Field(default_factory=ModelTimeouts)
    max_image_side_px: int = Field(1280, ge=256, le=4096)  # IDP-03 ch. 4.3
    max_image_bytes: ByteSize = ByteSize(20 * 1024 * 1024)  # larger page images are refused (AIG-422-INPUT-001)


class QueueSettings(BaseModel):
    """Fair queuing (IDP-03 ch. 4.5)."""

    wait_timeout: timedelta = Field(timedelta(seconds=20), ge=timedelta(seconds=1), le=timedelta(minutes=5))
    priority_share: int = Field(3, ge=1, le=10)  # PRIORITY is served this many times for every STANDARD
    lease: timedelta = Field(timedelta(minutes=3), ge=timedelta(seconds=10))  # a slot held longer is freed (crash)


class CapsSettings(BaseModel):
    """AI calls per account per day (IDP-03 ch. 4.5). Credits and plan limits are user-service's; these are ceilings."""

    guest: int = Field(10, ge=0)
    personal: int = Field(30, ge=0)
    corporate: int = Field(5000, ge=0)


class BreakerSettings(BaseModel):
    failures: int = Field(5, ge=1, le=100)  # within ``window`` open the breaker
    window: timedelta = Field(timedelta(seconds=30), ge=timedelta(seconds=1))
    reset_after: timedelta = Field(timedelta(seconds=30), ge=timedelta(seconds=1))  # then one probe is let through


class RedisSettings(BaseModel):
    url: SecretStr  # redis://host:6379/0


class StorageSettings(BaseModel):
    """MinIO, read-only for the gateway: page images are fetched by key (IDP-03 ch. 4.3)."""

    endpoint: HttpUrl
    access_key: SecretStr
    secret_key: SecretStr
    region: str = Field("us-east-1", min_length=1, max_length=32)
    allowed_buckets: list[str] = Field(default_factory=lambda: ["idp-originals", "idp-derived"], max_length=10)
    timeout: timedelta = Field(timedelta(seconds=30), ge=timedelta(seconds=1), le=timedelta(minutes=5))


class IssuerSettings(BaseModel):
    issuer: HttpUrl
    jwks_url: HttpUrl


class AuthSettings(BaseModel):
    """Service tokens only (SEC-07): client-credentials tokens whose ``azp`` is an allowed client."""

    issuers: list[IssuerSettings] = Field(default_factory=list, max_length=5)
    audience: str = Field("ai-gateway", min_length=1, max_length=128)
    allowed_clients: list[str] = Field(default_factory=lambda: ["extraction-service", "conversion-service"])
    jwks_cache: timedelta = Field(timedelta(minutes=10), ge=timedelta(minutes=1), le=timedelta(hours=24))
    leeway: timedelta = Field(timedelta(seconds=30), ge=timedelta(0), le=timedelta(minutes=5))


class MissingSettingsError(RuntimeError):
    """The process started without settings it needs."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_nested_delimiter="__", env_file=".env", extra="ignore")

    service_name: str = Field("ai-gateway", min_length=1, max_length=64)
    environment: Literal["local", "dev", "test", "staging", "prod"] = "local"
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    model: ModelSettings = Field(default_factory=ModelSettings)
    queue: QueueSettings = Field(default_factory=QueueSettings)
    caps: CapsSettings = Field(default_factory=CapsSettings)
    breaker: BreakerSettings = Field(default_factory=BreakerSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)
    redis: RedisSettings | None = None
    storage: StorageSettings | None = None

    def require_redis(self) -> RedisSettings:
        return _required(self.redis, "REDIS__URL")

    def require_storage(self) -> StorageSettings:
        return _required(self.storage, "STORAGE__ENDPOINT, STORAGE__ACCESS_KEY and STORAGE__SECRET_KEY")


def _required[T](group: T | None, variables: str) -> T:
    if group is None:
        raise MissingSettingsError(f"This process needs {variables}; see README.md")
    return group


@lru_cache
def get_settings() -> Settings:
    return Settings()
