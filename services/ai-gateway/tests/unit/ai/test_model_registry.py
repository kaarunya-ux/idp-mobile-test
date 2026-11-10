"""models.yaml (ML-01, ML-02): what it pins, what is refused, and the check of what a server serves."""

import hashlib
import json
from pathlib import Path

import httpx
import pytest

from ai_gateway.ai.infrastructure.model_registry import (
    ModelRegistry,
    ModelRegistryError,
    resolve_registry_path,
    served_model,
    sha256_of,
    verify_served_models,
)

REGISTRY_FILE = Path(__file__).resolve().parents[3] / "models.yaml"
GGUF = "Qwen3.6-35B-A3B-UD-IQ4_XS.gguf"
SHA = "a" * 64

ONE_MODEL = f"""
models:
  - name: test-model
    version: "Test 1"
    quantization: Q4
    source: example.com/test
    runtime: llama.cpp
    device: gpu
    served_as: test-model
    roles:
      - page
    files:
      - path: {GGUF}
        sha256: {SHA}
        size: 10
"""


def _llama_server(*names: str) -> httpx.AsyncClient:
    """A stand-in for llama-server's GET /v1/models, which lists the model's file path and its alias."""
    body = {
        "models": [{"name": n, "model": n} for n in names],
        "data": [{"id": n, "aliases": ["an-alias"]} for n in names],
    }
    return httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body)))


def test_pins_the_pre_prod_model_by_version_quantisation_and_checksum_when_the_repository_registry_is_read() -> None:
    registry = ModelRegistry.from_file(REGISTRY_FILE)

    spec = registry.for_role("page")
    assert spec is not None
    assert (spec.name, spec.version, spec.quantization) == ("qwen3.6-35b-a3b", "Qwen3.6-35B-A3B", "UD-IQ4_XS")
    assert [f.path for f in spec.files] == [GGUF]
    assert spec.files[0].sha256 == "649d7508507b84638732c4f52c24c8b15843c6dca2f3ff793ae07c14a67ebbb3"
    assert spec.files[0].size == 17_730_509_792
    assert {role for role in ("vl", "smart", "auto", "page") if registry.for_role(role) is spec} == {
        "vl",
        "smart",
        "auto",
        "page",
    }


def test_gives_the_same_fingerprint_until_a_checksum_changes() -> None:
    assert ModelRegistry.from_text(ONE_MODEL).fingerprint() == ModelRegistry.from_text(ONE_MODEL).fingerprint()
    assert (
        ModelRegistry.from_text(ONE_MODEL).fingerprint()
        != ModelRegistry.from_text(ONE_MODEL.replace(SHA, "b" * 64)).fingerprint()
    )


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (("sha256: " + SHA, "sha256: not-a-checksum"), "no valid SHA-256"),
        (("      - page", "      - nowhere"), "roles must be some of"),
        (("device: gpu", "device: tpu"), "device one of"),
        (("    quantization: Q4\n", ""), "lacks: quantization"),
        (("        size: 10", "        size: big"), "not a number of bytes"),
    ],
)
def test_refuses_a_registry_entry_that_is_incomplete_or_wrong(change: tuple[str, str], message: str) -> None:
    with pytest.raises(ModelRegistryError, match=message):
        ModelRegistry.from_text(ONE_MODEL.replace(*change))


def test_refuses_two_models_that_claim_the_same_role() -> None:
    second = ONE_MODEL.replace("name: test-model", "name: other-model")
    two = ONE_MODEL + second.split("models:\n", 1)[1]

    with pytest.raises(ModelRegistryError, match="one role to two models"):
        ModelRegistry.from_text(two)


def test_refuses_a_missing_or_empty_registry(tmp_path: Path) -> None:
    with pytest.raises(ModelRegistryError, match="could not be read"):
        ModelRegistry.from_file(tmp_path / "missing.yaml")
    with pytest.raises(ModelRegistryError, match="empty"):
        ModelRegistry.from_text("# only a comment\n")


def test_hashes_a_file_the_same_way_sha256sum_does(tmp_path: Path) -> None:
    model = tmp_path / GGUF
    model.write_bytes(b"not a model, just bytes" * 1000)

    assert sha256_of(model) == hashlib.sha256(model.read_bytes()).hexdigest()


async def test_lists_the_ids_paths_and_aliases_a_llama_server_reports() -> None:
    async with _llama_server("/models/qwen/" + GGUF) as client:
        served = await served_model(client, "http://model/v1")

    assert served is not None
    assert served.names == {"/models/qwen/" + GGUF, "an-alias"}
    assert served.file is None  # this stand-in has no /props


async def test_confirms_a_server_that_serves_the_registered_file() -> None:
    registry = ModelRegistry.from_text(ONE_MODEL)

    async with _llama_server("/Users/x/models/" + GGUF) as client:
        mismatched = await verify_served_models(registry, {"page": ("http://model/v1", None)}, client, "strict")

    assert mismatched == []


async def test_reports_a_server_that_serves_another_model_when_the_check_only_warns() -> None:
    registry = ModelRegistry.from_text(ONE_MODEL)

    async with _llama_server("/Users/x/models/Qwen3.5-4B-MLX-4bit") as client:
        mismatched = await verify_served_models(registry, {"page": ("http://model/v1", None)}, client, "warn")

    assert mismatched == ["page"]


async def test_stops_the_start_up_when_the_check_is_strict_and_a_server_serves_another_model() -> None:
    registry = ModelRegistry.from_text(ONE_MODEL)

    async with _llama_server("/Users/x/models/Qwen3.5-4B-MLX-4bit") as client:
        with pytest.raises(ModelRegistryError, match="page"):
            await verify_served_models(registry, {"page": ("http://model/v1", None)}, client, "strict")


async def test_only_logs_a_server_it_cannot_ask_because_the_breaker_already_handles_a_model_that_is_down() -> None:
    registry = ModelRegistry.from_text(ONE_MODEL)

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(refuse)) as client:
        mismatched = await verify_served_models(registry, {"page": ("http://model/v1", None)}, client, "strict")

    assert mismatched == []


async def test_does_not_ask_the_server_when_the_check_is_off() -> None:
    registry = ModelRegistry.from_text(ONE_MODEL)
    asked: list[str] = []

    def record(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        return httpx.Response(200, content=json.dumps({}))

    async with httpx.AsyncClient(transport=httpx.MockTransport(record)) as client:
        await verify_served_models(registry, {"page": ("http://model/v1", None)}, client, "off")

    assert asked == []


async def test_asks_a_server_again_until_it_answers_when_the_start_up_may_wait() -> None:
    """On a VM the gateway can start before llama-server has finished loading its model."""
    registry = ModelRegistry.from_text(ONE_MODEL)
    answers = iter([None, None, "/models/" + GGUF])
    slept: list[float] = []

    def sometimes_down(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/props":
            return httpx.Response(404)
        name = next(answers)
        if name is None:
            raise httpx.ConnectError("loading", request=request)
        return httpx.Response(200, json={"data": [{"id": name}]})

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    async with httpx.AsyncClient(transport=httpx.MockTransport(sometimes_down)) as client:
        mismatched = await verify_served_models(
            registry, {"page": ("http://model/v1", None)}, client, "strict", wait_seconds=60, sleep=fake_sleep
        )

    assert mismatched == []
    assert slept == [5.0, 5.0]


async def test_gives_up_waiting_after_the_allowed_time_and_only_logs() -> None:
    registry = ModelRegistry.from_text(ONE_MODEL)
    slept: list[float] = []

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    async with httpx.AsyncClient(transport=httpx.MockTransport(down)) as client:
        mismatched = await verify_served_models(
            registry, {"page": ("http://model/v1", None)}, client, "strict", wait_seconds=10, sleep=fake_sleep
        )

    assert mismatched == []
    assert slept == [5.0, 5.0]


def test_finds_a_relative_registry_when_the_working_directory_is_not_the_service_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)  # a systemd unit or a process manager starts the service from anywhere

    assert resolve_registry_path(Path("models.yaml")) == REGISTRY_FILE
    assert ModelRegistry.from_file(Path("models.yaml")).for_role("page") is not None
    assert resolve_registry_path(Path("/etc/idp/models.yaml")) == Path("/etc/idp/models.yaml")


def _server_with_props(alias: str, model_path: str) -> httpx.AsyncClient:
    """A llama-server as started in pre-prod: /v1/models lists only the --alias, /props names the model file."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"models": [{"name": alias, "model": alias}], "data": [{"id": alias}]})
        return httpx.Response(200, json={"model_alias": alias, "model_path": model_path, "build_info": "b11429"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_recognises_the_model_by_the_file_the_server_reports_when_its_alias_is_a_host_choice() -> None:
    """The alias is a start-up flag and differs between hosts (qwen3.6-35b here, qwen3.6-35b-a3b there); the file name
    in /props is the identity. Found against the real pre-prod server, whose /v1/models lists only the alias."""
    registry = ModelRegistry.from_text(ONE_MODEL)

    async with _server_with_props("some-host-alias", "models/qwen/Qwen3.6-35B-A3B-GGUF/" + GGUF) as client:
        mismatched = await verify_served_models(registry, {"page": ("http://model:8080/v1", None)}, client, "strict")

    assert mismatched == []


async def test_catches_a_different_model_file_behind_the_registered_alias() -> None:
    """Someone swaps the model on the host and keeps the alias: the file the server reports decides."""
    registry = ModelRegistry.from_text(ONE_MODEL)

    async with _server_with_props("test-model", "models/other/Some-Other-Model-Q8_0.gguf") as client:
        mismatched = await verify_served_models(registry, {"page": ("http://model:8080/v1", None)}, client, "warn")

    assert mismatched == ["page"]


async def test_accepts_one_of_several_aliases_when_the_server_does_not_report_its_file() -> None:
    several = ONE_MODEL.replace("served_as: test-model", "served_as:\n      - first-alias\n      - second-alias")
    registry = ModelRegistry.from_text(several)

    async with _llama_server("second-alias") as client:  # a server without /props: the alias is all it says
        mismatched = await verify_served_models(registry, {"page": ("http://model:8080/v1", None)}, client, "strict")

    assert mismatched == []
    assert registry.for_role("page").served_as == ("first-alias", "second-alias")  # type: ignore[union-attr]
