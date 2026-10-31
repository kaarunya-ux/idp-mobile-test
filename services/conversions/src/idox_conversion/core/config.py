"""Service settings, read once from the environment and validated at startup (standard ch. 9).

Every environment variable is listed in README.md (CFG-05). Nested groups use a double underscore:
``MODEL__BASE_URL=http://host:8080`` sets ``Settings.model.base_url``. Every threshold a conversion decision uses lives
here and not in code (ML-07); changing one needs an evaluation report (ML-12).
"""

from datetime import timedelta
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AnyHttpUrl, BaseModel, Field, SecretStr, TypeAdapter
from pydantic_settings import BaseSettings, SettingsConfigDict

_LOCAL_MODEL_URL = TypeAdapter(AnyHttpUrl).validate_python("http://127.0.0.1:8080")


class LoggingSettings(BaseModel):
    """How the service writes its logs (OBS-01): JSON in every environment except local development."""

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    format: Literal["json", "console"] = "json"


class ModelSettings(BaseModel):
    """The vision-language model server that reads pages (LLM-01, LLM-05, ML-06).

    ``thinking`` is always sent to the server, never left to its default: a server restarted with thinking on spent the
    whole output budget on hidden reasoning and 15 of 32 conversions failed (see docs/build-log.md).
    """

    base_url: AnyHttpUrl = _LOCAL_MODEL_URL
    api_key: SecretStr | None = None
    thinking: bool = False
    request_timeout: timedelta = Field(timedelta(seconds=600), ge=timedelta(seconds=10), le=timedelta(minutes=30))
    max_output_tokens: int = Field(4000, ge=256, le=32_000)
    max_image_side_px: int = Field(0, ge=0, le=8000)  # 0 = as drawn; smaller = fewer image tokens
    concurrency: int = Field(1, ge=1, le=8)  # requests in flight; the local server reads one page at a time
    retries: int = Field(2, ge=0, le=5)  # extra tries after a refused connection or a 502 / 503 / 504 (LLM-05)
    prompts_dir: Path = Path("prompts")  # versioned prompt files (LLM-02)
    name: str = Field("qwen3.6-35b-a3b", min_length=1)  # the model, recorded on every output (ML-04)
    version: str = Field("UD-IQ4_XS", min_length=1)


class LimitsSettings(BaseModel):
    """Caps that protect the workers from oversized input and runaway work (IDP-03 ch. 3.7, FIL-05, ML-06).

    The document and job-item timeouts and ``max_image_pixels`` are the numbers in IDP-03 ch. 3.7. ``max_pages`` and
    ``max_sheet_cells`` are not in IDP-03 (it says "page and sheet-size caps" without numbers): ADR 0003 item 5.
    """

    max_image_pixels: int = Field(180_000_000, ge=1_000_000, le=1_000_000_000)
    document_timeout: timedelta = Field(timedelta(seconds=120), ge=timedelta(seconds=1), le=timedelta(minutes=30))
    job_item_timeout: timedelta = Field(timedelta(minutes=10), ge=timedelta(seconds=1), le=timedelta(hours=2))
    max_pages: int = Field(200, ge=1, le=10_000)
    max_sheet_cells: int = Field(5_000_000, ge=1_000, le=100_000_000)


class ChecksSettings(BaseModel):
    """When a finished conversion counts as good: the share of the source's words that must reach the output."""

    min_word_share: float = Field(0.95, gt=0, le=1)  # a source with a text layer
    min_word_share_ocr: float = Field(0.85, gt=0, le=1)  # a scan or image: the source is itself an OCR reading
    ocr_min_word_chars: int = Field(3, ge=1, le=10)  # OCR fragments shorter than this (not numbers) are speckle


class LayoutSettings(BaseModel):
    """Numbers the layout steps use to match the output to the source's page."""

    max_paragraph_space_pt: float = Field(36.0, gt=0, le=200)  # no paragraph carries more space than this
    running_line_share: float = Field(0.6, gt=0, le=1)  # a margin line is "running" when this share of pages repeat it
    footer_zone_from: float = Field(0.92, gt=0.5, lt=1)  # share of the page height from which a line is in the footer
    header_zone_to: float = Field(0.06, gt=0, lt=0.5)  # share of the page height up to which a line is in the header
    max_margin_share: float = Field(0.25, gt=0, lt=0.5)  # a side margin wider than this share of the page is not one
    min_crop_height_lines: float = Field(0.6, gt=0, le=2)  # a re-read strip shorter than this many lines is skipped
    reread_tokens_base: int = Field(300, ge=50, le=4000)  # output budget of a re-read crop...
    reread_tokens_per_line: int = Field(120, ge=10, le=1000)  # ...plus this for each line in it


class Settings(BaseSettings):
    """All configuration for one process. Built once by ``get_settings()``; invalid values stop the process."""

    model_config = SettingsConfigDict(env_nested_delimiter="__", env_file=".env", extra="ignore")

    service_name: str = Field("idox-conversion-service", min_length=1, max_length=64)
    environment: Literal["local", "dev", "test", "staging", "prod"] = "local"
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    model: ModelSettings = Field(default_factory=ModelSettings)
    limits: LimitsSettings = Field(default_factory=LimitsSettings)
    checks: ChecksSettings = Field(default_factory=ChecksSettings)
    layout: LayoutSettings = Field(default_factory=LayoutSettings)


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings, reading the environment on the first call only."""
    return Settings()
