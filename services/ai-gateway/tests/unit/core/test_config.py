"""Settings come from the environment with safe defaults (CFG-01)."""

import pytest

from ai_gateway.core.config import MissingSettingsError, Settings


def test_uses_the_documented_defaults_when_nothing_is_set() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]  # pydantic-settings' init-only argument

    assert str(settings.model.base_url) == "http://10.0.3.66:4000/v1"
    assert settings.model.vl_max_concurrent == 4
    assert settings.queue.wait_timeout.total_seconds() == 20
    assert (settings.caps.guest, settings.caps.personal) == (10, 30)
    assert settings.auth.audience == "ai-gateway"


def test_reads_nested_variables_when_they_are_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODEL__VL_MAX_CONCURRENT", "8")
    monkeypatch.setenv("REDIS__URL", "redis://localhost:6379/0")

    settings = Settings(_env_file=None)  # type: ignore[call-arg]  # pydantic-settings' init-only argument

    assert settings.model.vl_max_concurrent == 8
    assert settings.require_redis().url.get_secret_value() == "redis://localhost:6379/0"


def test_raises_when_storage_is_required_but_not_set() -> None:
    with pytest.raises(MissingSettingsError):
        Settings(_env_file=None).require_storage()  # type: ignore[call-arg]  # pydantic-settings' init-only argument
