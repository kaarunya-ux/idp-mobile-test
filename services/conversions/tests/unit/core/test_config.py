"""Settings: defaults, nested environment variables and refusal of invalid values (CFG-01, CFG-05)."""

from datetime import timedelta

import pytest
from pydantic import ValidationError

from idox_conversion.core.config import Settings


def test_defaults_match_the_numbers_in_idp_03_when_nothing_is_set() -> None:
    settings = Settings(_env_file=None)

    assert settings.limits.max_image_pixels == 180_000_000
    assert settings.limits.document_timeout == timedelta(seconds=120)
    assert settings.limits.job_item_timeout == timedelta(minutes=10)


def test_thinking_is_off_when_nothing_is_set() -> None:
    assert Settings(_env_file=None).model.thinking is False


def test_nested_value_is_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODEL__BASE_URL", "http://10.0.0.5:8080")
    monkeypatch.setenv("LIMITS__MAX_PAGES", "7")

    settings = Settings(_env_file=None)

    assert str(settings.model.base_url) == "http://10.0.0.5:8080/"
    assert settings.limits.max_pages == 7


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("CHECKS__MIN_WORD_SHARE", "1.5"),
        ("CHECKS__MIN_WORD_SHARE_OCR", "0"),
        ("LAYOUT__FOOTER_ZONE_FROM", "0.2"),
        ("LIMITS__MAX_PAGES", "0"),
        ("MODEL__MAX_OUTPUT_TOKENS", "10"),
    ],
)
def test_invalid_value_stops_startup_when_out_of_range(monkeypatch: pytest.MonkeyPatch, name: str, value: str) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_scan_word_share_is_looser_than_digital_by_default() -> None:
    checks = Settings(_env_file=None).checks

    assert checks.min_word_share_ocr < checks.min_word_share
