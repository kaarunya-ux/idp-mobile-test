"""Settings come only from the environment and stop the process when invalid (CFG-01, CFG-04)."""

import pytest
from pydantic import ValidationError

from conversion.core.config import Settings, get_settings


def _settings() -> Settings:
    return Settings(_env_file=None)  # ignore any developer .env so tests see only what they set


def test_uses_safe_defaults_when_nothing_is_set() -> None:
    settings = _settings()

    assert settings.service_name == "conversion-service"
    assert settings.environment == "local"
    assert settings.logging.level == "INFO"
    assert settings.logging.format == "json"


def test_reads_nested_values_when_set_with_double_underscore(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOGGING__LEVEL", "DEBUG")
    monkeypatch.setenv("LOGGING__FORMAT", "console")
    monkeypatch.setenv("ENVIRONMENT", "dev")

    settings = _settings()

    assert settings.logging.level == "DEBUG"
    assert settings.logging.format == "console"
    assert settings.environment == "dev"


@pytest.mark.parametrize(
    ("variable", "value"),
    [("ENVIRONMENT", "production"), ("LOGGING__LEVEL", "LOUD"), ("LOGGING__FORMAT", "xml"), ("SERVICE_NAME", "")],
)
def test_refuses_to_start_when_a_value_is_invalid(monkeypatch: pytest.MonkeyPatch, variable: str, value: str) -> None:
    monkeypatch.setenv(variable, value)

    with pytest.raises(ValidationError):
        _settings()


def test_returns_the_same_settings_when_called_twice() -> None:
    get_settings.cache_clear()

    assert get_settings() is get_settings()
