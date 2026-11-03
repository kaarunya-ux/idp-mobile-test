"""The model server client on recorded replies (respx, TST-04)."""

import json
from dataclasses import replace

import httpx
import pytest
import respx

from ai_gateway.ai.domain.exceptions import ModelUnavailableError
from ai_gateway.ai.domain.models import Image, ModelRequest, ModelRole
from ai_gateway.ai.infrastructure.breaker import BreakerState, CircuitBreaker
from ai_gateway.ai.infrastructure.model_client import OpenAiModelClient

BASE = "http://model.test/v1/"
REQUEST = ModelRequest(
    role=ModelRole.VL,
    system="system prompt",
    text="question",
    images=[Image(data=b"\xff\xd8jpeg")],
    schema={"type": "object"},
    schema_name="fields",
    temperature=0,
    max_tokens=64,
    follow_up=(("assistant", "bad"), ("user", "again")),
)
REPLY = {
    "model": "lfm2-vl",
    "choices": [{"message": {"content": '{"a": 1}'}}],
    "usage": {"prompt_tokens": 120, "completion_tokens": 7},
}


def _client(breaker: CircuitBreaker | None = None) -> OpenAiModelClient:
    http = httpx.AsyncClient(base_url=BASE, timeout=httpx.Timeout(5, connect=1))
    breaker = breaker or CircuitBreaker(failures=2, window_s=30, reset_after_s=30)
    return OpenAiModelClient(http, models={ModelRole.VL: "vl"}, breaker=breaker)


@respx.mock
async def test_sends_schema_image_and_history_and_reads_tokens_when_the_server_answers() -> None:
    route = respx.post(f"{BASE}chat/completions").mock(return_value=httpx.Response(200, json=REPLY))

    reply = await _client().complete(REQUEST, timeout_s=10)

    body = json.loads(route.calls[0].request.content)
    assert body["model"] == "vl"
    assert body["response_format"]["json_schema"]["name"] == "fields"
    assert body["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert [m["role"] for m in body["messages"]] == ["system", "user", "assistant", "user"]
    assert (reply.model, reply.content, reply.tokens.input, reply.tokens.output) == ("lfm2-vl", '{"a": 1}', 120, 7)


@respx.mock
async def test_opens_the_breaker_when_the_server_keeps_failing() -> None:
    respx.post(f"{BASE}chat/completions").mock(return_value=httpx.Response(503))
    breaker = CircuitBreaker(failures=2, window_s=30, reset_after_s=30)
    client = _client(breaker)

    for _ in range(2):
        with pytest.raises(ModelUnavailableError):
            await client.complete(REQUEST, timeout_s=10)

    assert breaker.state is BreakerState.OPEN


@respx.mock
async def test_raises_unavailable_when_the_server_times_out() -> None:
    respx.post(f"{BASE}chat/completions").mock(side_effect=httpx.ReadTimeout("slow"))

    with pytest.raises(ModelUnavailableError):
        await _client().complete(REQUEST, timeout_s=10)


@respx.mock
async def test_does_not_count_a_refused_request_against_the_server() -> None:
    respx.post(f"{BASE}chat/completions").mock(return_value=httpx.Response(400, json={"error": "bad"}))
    breaker = CircuitBreaker(failures=1, window_s=30, reset_after_s=30)

    with pytest.raises(ModelUnavailableError):
        await _client(breaker).complete(REQUEST, timeout_s=10)
    assert breaker.state is BreakerState.CLOSED


@respx.mock
async def test_raises_unavailable_when_the_reply_has_no_choices() -> None:
    respx.post(f"{BASE}chat/completions").mock(return_value=httpx.Response(200, json={"choices": []}))

    with pytest.raises(ModelUnavailableError):
        await _client().complete(REQUEST, timeout_s=10)


@respx.mock
async def test_leaves_the_schema_out_when_the_host_breaks_it() -> None:
    route = respx.post(f"{BASE}chat/completions").mock(return_value=httpx.Response(200, json=REPLY))
    http = httpx.AsyncClient(base_url=BASE, timeout=httpx.Timeout(5, connect=1))
    breaker = CircuitBreaker(failures=2, window_s=30, reset_after_s=30)

    await OpenAiModelClient(http, models={ModelRole.VL: "vl"}, breaker=breaker, send_schema=False).complete(
        REQUEST, timeout_s=10
    )

    assert "response_format" not in json.loads(route.calls[0].request.content)


@respx.mock
async def test_switches_thinking_off_in_both_servers_forms_unless_the_prompt_says_otherwise() -> None:
    route = respx.post(f"{BASE}chat/completions").mock(return_value=httpx.Response(200, json=REPLY))
    http = httpx.AsyncClient(base_url=BASE, timeout=httpx.Timeout(5, connect=1))
    breaker = CircuitBreaker(failures=5, window_s=30, reset_after_s=30)
    client = OpenAiModelClient(http, models={ModelRole.VL: "vl"}, breaker=breaker, thinking=False)

    await client.complete(REQUEST, timeout_s=10)
    await client.complete(replace(REQUEST, thinking=True), timeout_s=10)

    default, prompt_says = (json.loads(call.request.content) for call in route.calls)
    assert (default["chat_template_kwargs"], default["enable_thinking"]) == ({"enable_thinking": False}, False)
    assert (prompt_says["chat_template_kwargs"], prompt_says["enable_thinking"]) == ({"enable_thinking": True}, True)
