"""The model client: request shape, retries, validation and what it logs (LLM-01, LLM-03, LLM-05, LLM-06, ML-06)."""

import json
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from idox_conversion.conversion.domain.exceptions import ModelAnswerInvalidError, ModelUnavailableError
from idox_conversion.conversion.infrastructure.model.llama_client import LlamaServerPageReader
from idox_conversion.conversion.infrastructure.model.prompts import load_prompt
from idox_conversion.core.config import ModelSettings

_PROMPTS = Path(__file__).resolve().parents[4] / "prompts"
_PICTURE = b"\x89PNG\r\n\x1a\nnot-a-real-picture"
_GOOD = {"analysis": {"n_blocks": 1}, "blocks": [{"kind": "paragraph", "text": "SECRET DOCUMENT TEXT"}]}


def _reader(handler: httpx.MockTransport, **settings: object) -> LlamaServerPageReader:
    config = ModelSettings(retries=2, request_timeout=timedelta(seconds=10), **settings)  # type: ignore[arg-type]  # test-only: **settings is a loose bag of ModelSettings fields
    return LlamaServerPageReader(
        config,
        load_prompt(_PROMPTS, "read_page", 1),
        load_prompt(_PROMPTS, "read_short_page", 1),
        client=httpx.Client(transport=handler),
    )


def _reply(content: object, finish: str = "stop") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"finish_reason": finish, "message": {"content": json.dumps(content)}}],
            "usage": {"prompt_tokens": 2000, "completion_tokens": 40},
        },
    )


def test_page_is_read_and_carries_its_provenance_and_token_counts() -> None:
    reading = _reader(httpx.MockTransport(lambda _: _reply(_GOOD))).read_page(_PICTURE)

    assert reading.page.blocks[0].text == "SECRET DOCUMENT TEXT"  # type: ignore[union-attr]  # test-only: the first block is known to be text
    assert reading.provenance.model_name == "qwen3.6-35b-a3b"
    assert (reading.provenance.prompt_name, reading.provenance.prompt_version) == ("read_page", 1)
    assert (reading.prompt_tokens, reading.completion_tokens) == (2000, 40)


@pytest.mark.parametrize("thinking", [False, True])
def test_thinking_is_sent_on_every_request_from_settings(thinking: bool) -> None:
    seen: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return _reply(_GOOD)

    _reader(httpx.MockTransport(handler), thinking=thinking).read_page(_PICTURE)

    assert seen[0]["chat_template_kwargs"] == {"enable_thinking": thinking}
    assert seen[0]["response_format"]["type"] == "json_schema"  # type: ignore[index]  # test-only: the request body is plain JSON


def test_short_page_uses_the_short_prompt() -> None:
    seen: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return _reply(_GOOD)

    reading = _reader(httpx.MockTransport(handler)).read_page(_PICTURE, short_page=True)

    assert reading.provenance.prompt_name == "read_short_page"


def test_api_key_is_sent_as_a_bearer_token() -> None:
    headers: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        headers.append(request.headers.get("authorization", ""))
        return _reply(_GOOD)

    _reader(httpx.MockTransport(handler), api_key=SecretStr("k-123")).read_page(_PICTURE)

    assert headers == ["Bearer k-123"]


def test_server_that_is_busy_twice_then_answers_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tenacity.nap.time.sleep", lambda _: None)
    answers = iter([httpx.Response(503), httpx.Response(503), _reply(_GOOD)])

    reading = _reader(httpx.MockTransport(lambda _: next(answers))).read_page(_PICTURE)

    assert reading.completion_tokens == 40


def test_server_that_never_answers_is_unavailable_after_the_allowed_tries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tenacity.nap.time.sleep", lambda _: None)
    calls: list[int] = []

    def handler(_: httpx.Request) -> httpx.Response:
        calls.append(1)
        raise httpx.ConnectError("refused")

    with pytest.raises(ModelUnavailableError):
        _reader(httpx.MockTransport(handler)).read_page(_PICTURE)

    assert len(calls) == 3  # one try and two retries


def test_server_error_that_is_not_transient_is_not_retried() -> None:
    calls: list[int] = []

    def handler(_: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(500)

    with pytest.raises(ModelUnavailableError):
        _reader(httpx.MockTransport(handler)).read_page(_PICTURE)

    assert len(calls) == 1


def test_answer_cut_off_at_the_output_limit_is_an_error() -> None:
    with pytest.raises(ModelAnswerInvalidError, match="cut off"):
        _reader(httpx.MockTransport(lambda _: _reply(_GOOD, finish="length"))).read_page(_PICTURE)


def test_answer_that_is_not_the_schema_is_an_error() -> None:
    with pytest.raises(ModelAnswerInvalidError):
        _reader(httpx.MockTransport(lambda _: _reply({"blocks": "nope"}))).read_page(_PICTURE)


def test_logs_never_carry_the_document_text(capsys: pytest.CaptureFixture[str]) -> None:
    from idox_conversion.core.config import LoggingSettings  # noqa: PLC0415
    from idox_conversion.core.logging import configure_logging  # noqa: PLC0415

    configure_logging(LoggingSettings(level="INFO", format="json"))

    _reader(httpx.MockTransport(lambda _: _reply(_GOOD))).read_page(_PICTURE)

    out = capsys.readouterr().out
    assert "model_page_read" in out
    assert "SECRET DOCUMENT TEXT" not in out
