"""The service token for calls to ai-gateway (SEC-07): fetched with client credentials, cached, renewed."""

import httpx
import pytest
from pydantic import SecretStr

from conversion.core.config import ServiceAuthSettings
from conversion.core.service_auth import ServiceTokenError, ServiceTokenProvider

AUTH = ServiceAuthSettings.model_validate(
    {"token_url": "http://keycloak:8080/realms/idp/token", "client_secret": SecretStr("test-secret")}
)


def _provider(
    fetched: list[dict[str, str]], *, status: int = 200, now: list[float] | None = None
) -> ServiceTokenProvider:
    def handler(request: httpx.Request) -> httpx.Response:
        fetched.append(dict(httpx.QueryParams(request.content.decode())))
        return httpx.Response(status, json={"access_token": f"token-{len(fetched)}", "expires_in": 300})

    clock = now if now is not None else [0.0]
    return ServiceTokenProvider(httpx.Client(transport=httpx.MockTransport(handler)), AUTH, lambda: clock[0])


def test_asks_keycloak_once_as_conversion_service_and_caches_the_token() -> None:
    fetched: list[dict[str, str]] = []
    tokens = _provider(fetched)

    assert tokens.token() == tokens.token() == "token-1"
    assert fetched == [
        {
            "grant_type": "client_credentials",
            "client_id": "conversion-service",
            "client_secret": "test-secret",
            "audience": "ai-gateway",
        }
    ]


def test_renews_the_token_shortly_before_it_expires_or_after_it_is_dropped() -> None:
    fetched: list[dict[str, str]] = []
    now = [0.0]
    tokens = _provider(fetched, now=now)
    tokens.token()

    now[0] = 271  # 30 s before the 300 s expiry
    assert tokens.token() == "token-2"
    tokens.invalidate()
    assert tokens.token() == "token-3"


def test_raises_a_token_error_when_keycloak_refuses() -> None:
    with pytest.raises(ServiceTokenError):
        _provider([], status=401).token()
