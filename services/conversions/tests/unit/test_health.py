"""Both probes answer UP without dependencies (DPL-03)."""

from fastapi.testclient import TestClient

from idox_conversion.main import create_app


def test_liveness_is_up() -> None:
    response = TestClient(create_app()).get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "UP", "checks": {}}


def test_readiness_is_up() -> None:
    response = TestClient(create_app()).get("/health/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "UP"
