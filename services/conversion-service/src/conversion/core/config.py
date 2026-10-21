"""Service settings, read once from the environment and validated at startup (standard ch. 9).

Every environment variable is listed in README.md (CFG-05). Nested groups use a double underscore:
``LOGGING__LEVEL=DEBUG`` sets ``Settings.logging.level``.
"""

from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field
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


class Settings(BaseSettings):
    """All configuration for one process. Built once by ``get_settings()``; invalid values stop the process."""

    model_config = SettingsConfigDict(env_nested_delimiter="__", env_file=".env", extra="ignore")

    service_name: str = Field("conversion-service", min_length=1, max_length=64)
    environment: Literal["local", "dev", "test", "staging", "prod"] = "local"
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    limits: LimitsSettings = Field(default_factory=LimitsSettings)


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings, reading the environment on the first call only."""
    return Settings()
