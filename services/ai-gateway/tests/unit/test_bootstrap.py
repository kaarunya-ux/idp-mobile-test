"""The real wiring builds without contacting anything (clients connect lazily)."""

from ai_gateway.bootstrap import AiServices, build
from ai_gateway.core.config import Settings
from support.fakes import FixedClock


async def test_wires_every_service_when_redis_and_storage_are_configured() -> None:
    settings = Settings.model_validate(
        {
            "redis": {"url": "redis://localhost:6379/0"},
            "storage": {"endpoint": "http://localhost:9000", "access_key": "local-key", "secret_key": "local-secret"},
            "model": {"api_key": "local-model-key"},
        }
    )

    container = build(settings, FixedClock())

    assert isinstance(container.ai, AiServices)
    assert container.http is not None
    assert container.http.headers["Authorization"] == "Bearer local-model-key"
    await container.close()


async def test_opens_the_page_models_own_server_when_one_is_set() -> None:
    settings = Settings.model_validate(
        {
            "redis": {"url": "redis://localhost:6379/0"},
            "storage": {"endpoint": "http://localhost:9000", "access_key": "local-key", "secret_key": "local-secret"},
            "model": {"page": {"base_url": "http://10.0.3.154:8080/v1", "model": "qwen3.6-35b-a3b"}},
        }
    )

    container = build(settings, FixedClock())

    assert container.page_http is not None
    assert str(container.page_http.base_url) == "http://10.0.3.154:8080/v1/"
    await container.close()


async def test_uses_the_main_server_for_pages_when_no_page_server_is_set() -> None:
    settings = Settings.model_validate(
        {
            "redis": {"url": "redis://localhost:6379/0"},
            "storage": {"endpoint": "http://localhost:9000", "access_key": "local-key", "secret_key": "local-secret"},
        }
    )

    container = build(settings, FixedClock())

    assert container.page_http is None
    await container.close()
