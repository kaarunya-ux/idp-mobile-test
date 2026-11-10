"""How the gateway reads models.yaml at start-up and where it asks each role's server which model it serves."""

from pathlib import Path

import pytest

from ai_gateway.ai.infrastructure.model_registry import ModelRegistryError
from ai_gateway.core.config import Settings
from ai_gateway.main import _load_registry, _servers_by_role

REGISTRY_FILE = Path(__file__).resolve().parents[2] / "models.yaml"


def _settings(**model: object) -> Settings:
    return Settings.model_validate({"model": model})


def test_sends_the_page_role_to_its_own_server_when_page_read_has_one() -> None:
    settings = _settings(
        base_url="http://cards:8082/v1", page={"base_url": "http://pages:8080/v1", "api_key": "page-key"}
    )

    servers = _servers_by_role(settings)

    assert servers["vl"] == ("http://cards:8082/v1", None)
    assert servers["page"] == ("http://pages:8080/v1", "page-key")


def test_sends_the_page_role_to_the_main_server_when_page_read_has_none() -> None:
    servers = _servers_by_role(_settings(base_url="http://one:8080/v1"))

    assert servers["page"] == servers["vl"] == ("http://one:8080/v1", None)


def test_reads_the_registry_when_the_file_is_there() -> None:
    registry = _load_registry(_settings(registry_file=str(REGISTRY_FILE)))

    assert registry is not None
    assert registry.for_role("page") is not None


def test_runs_without_a_registry_when_the_file_is_missing_and_the_check_is_off(tmp_path: Path) -> None:
    assert _load_registry(_settings(registry_file=str(tmp_path / "none.yaml"), registry_check="off")) is None


@pytest.mark.parametrize("mode", ["warn", "strict"])
def test_stops_the_start_up_when_the_check_is_on_and_the_registry_file_is_missing(mode: str, tmp_path: Path) -> None:
    with pytest.raises(ModelRegistryError, match="does not exist"):
        _load_registry(_settings(registry_file=str(tmp_path / "none.yaml"), registry_check=mode))
