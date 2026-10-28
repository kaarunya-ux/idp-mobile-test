"""The AI fallback through ai-gateway, and the service token it sends, against fake HTTP servers."""

import json
from collections.abc import Callable
from uuid import UUID

import httpx
import pytest
from pydantic import SecretStr

from extraction.core.config import ServiceAuthSettings
from extraction.core.service_auth import ServiceTokenError, ServiceTokenProvider
from extraction.pipeline.domain.exceptions import AiUnavailableError
from extraction.pipeline.domain.jobs import ObjectRef
from extraction.pipeline.domain.ports import AiCallContext
from extraction.pipeline.infrastructure.ai.gateway_reader import GatewayFieldReader
from idp_ocr_engines.model import PageImage

ACCOUNT = UUID("0192f0aa-0000-7000-8000-000000000001")
PAGE = PageImage(width=4, height=2, rgb=bytes(4 * 2 * 3))
CONTEXT = AiCallContext(
    account_id=ACCOUNT,
    priority=True,
    page_refs=(
        ObjectRef(bucket="idp-derived", key="a/d/i/page-0.png"),
        ObjectRef(bucket="idp-derived", key="a/d/i/page-1.png"),
    ),
    labels={"pan_number": "PAN"},
)
AUTH = ServiceAuthSettings.model_validate(
    {"token_url": "http://keycloak/token", "client_secret": SecretStr("test-secret")}
)


def _tokens(fetched: list[dict[str, str]], *, status: int = 200, lifetime: int = 300) -> ServiceTokenProvider:
    def handler(request: httpx.Request) -> httpx.Response:
        fetched.append(dict(httpx.QueryParams(request.content.decode())))
        return httpx.Response(status, json={"access_token": f"token-{len(fetched)}", "expires_in": lifetime})

    now = [0.0]
    provider = ServiceTokenProvider(httpx.Client(transport=httpx.MockTransport(handler)), AUTH, lambda: now[0])
    provider.now = now  # type: ignore[attr-defined]  # lets a test move the clock
    return provider


def _reader(answer: Callable[[httpx.Request], httpx.Response]) -> tuple[GatewayFieldReader, list[httpx.Request]]:
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return answer(request)

    client = httpx.Client(base_url="http://ai-gateway:8002", transport=httpx.MockTransport(handler))
    return GatewayFieldReader(client, _tokens([])), sent


OK = {
    "fields": [{"key": "pan_number", "value": "ABCPE1234F"}, {"key": "dob", "value": None}],
    "model": "vl",
    "promptVersion": "field-read.v1",
    "latencyMs": 900,
    "tokens": {"in": 1200, "out": 30},
}


def test_sends_keys_account_priority_and_token_and_returns_the_reading_when_the_gateway_answers() -> None:
    reader, sent = _reader(lambda _: httpx.Response(200, json=OK))

    reading = reader.read_fields("PAN_CARD", [PAGE], ["pan_number", "dob", "name"], CONTEXT)

    body = json.loads(sent[0].content)
    assert sent[0].url.path == "/internal/v1/ai/field-read"
    assert sent[0].headers["Authorization"] == "Bearer token-1"
    assert (body["accountId"], body["priority"], body["documentType"]) == (str(ACCOUNT), "PRIORITY", "PAN_CARD")
    assert body["pages"][1] == {"bucket": "idp-derived", "key": "a/d/i/page-1.png"}
    assert body["fields"][0] == {"key": "pan_number", "label": "PAN"}
    assert reading.values == {"pan_number": "ABCPE1234F", "dob": None, "name": None}
    assert (reading.model_id, reading.prompt_version) == ("vl", "field-read.v1")


@pytest.mark.parametrize("status", [429, 502, 503, 401])
def test_raises_ai_unavailable_when_the_gateway_refuses(status: int) -> None:
    reader, _ = _reader(lambda _: httpx.Response(status, json={"code": "AIG-503-BUSY-001"}))

    with pytest.raises(AiUnavailableError):
        reader.read_fields("PAN_CARD", [PAGE], ["pan_number"], CONTEXT)


def test_raises_ai_unavailable_when_the_gateway_times_out_or_answers_garbage() -> None:
    def timeout(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    for answer in (timeout, lambda _: httpx.Response(200, json={"nope": 1}), lambda _: httpx.Response(200, text="x")):
        reader, _ = _reader(answer)
        with pytest.raises(AiUnavailableError):
            reader.read_fields("PAN_CARD", [PAGE], ["pan_number"], CONTEXT)


def test_caches_the_service_token_until_shortly_before_it_expires() -> None:
    fetched: list[dict[str, str]] = []
    tokens = _tokens(fetched, lifetime=300)

    assert tokens.token() == tokens.token() == "token-1"
    tokens.now[0] = 271  # type: ignore[attr-defined]  # 30 s before expiry
    assert tokens.token() == "token-2"
    assert fetched[0] == {
        "grant_type": "client_credentials",
        "client_id": "extraction-service",
        "client_secret": "test-secret",
        "audience": "ai-gateway",
    }


def test_fetches_a_new_token_after_it_is_invalidated() -> None:
    fetched: list[dict[str, str]] = []
    tokens = _tokens(fetched)
    tokens.token()
    tokens.invalidate()

    assert tokens.token() == "token-2"


def test_raises_a_token_error_when_keycloak_refuses() -> None:
    with pytest.raises(ServiceTokenError):
        _tokens([], status=401).token()
