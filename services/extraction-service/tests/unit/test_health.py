"""The probe endpoints: shape and status codes the platform relies on (DPL-03)."""

import json
from collections.abc import AsyncIterator
from http import HTTPStatus

import pytest
from httpx import ASGITransport, AsyncClient

from extraction.main import create_app


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


async def test_sends_no_cors_headers_when_a_browser_origin_asks(client: AsyncClient) -> None:
    response = await client.get("/health/live", headers={"Origin": "http://10.0.3.40:5173"})

    assert "access-control-allow-origin" not in response.headers  # CORS lives at the edge only (SEC-09)


async def test_logs_start_and_stop_when_the_server_starts_and_stops(capsys: pytest.CaptureFixture[str]) -> None:
    app = create_app()

    async with app.router.lifespan_context(app):
        pass

    events = [json.loads(line)["event"] for line in capsys.readouterr().out.strip().splitlines()]
    assert events == ["api_started", "api_stopped"]
