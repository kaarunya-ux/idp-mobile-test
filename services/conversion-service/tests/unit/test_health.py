"""The probe endpoints: shape and status codes the platform relies on (DPL-03)."""

import json
from collections.abc import AsyncIterator
from http import HTTPStatus

import pytest
from httpx import ASGITransport, AsyncClient

from conversion.health import create_app


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as http:
        yield http


async def test_live_returns_up_when_process_is_running(client: AsyncClient) -> None:
    response = await client.get("/health/live")

    assert response.status_code == HTTPStatus.OK
    assert response.json() == {"status": "UP", "checks": {}}


async def test_ready_returns_up_when_there_are_no_dependencies_yet(client: AsyncClient) -> None:
    response = await client.get("/health/ready")

    assert response.status_code == HTTPStatus.OK
    assert response.json()["status"] == "UP"


async def test_api_docs_are_not_served_when_service_has_no_public_api(client: AsyncClient) -> None:
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert (await client.get(path)).status_code == HTTPStatus.NOT_FOUND


async def test_logs_start_and_stop_when_the_server_starts_and_stops(capsys: pytest.CaptureFixture[str]) -> None:
    app = create_app()

    async with app.router.lifespan_context(app):
        pass

    events = [json.loads(line)["event"] for line in capsys.readouterr().out.strip().splitlines()]
    assert events == ["health_server_started", "health_server_stopped"]
