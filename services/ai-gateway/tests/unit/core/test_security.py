"""Service tokens (SEC-01, SEC-07): signature, issuer, audience, expiry, and an allowed calling client."""

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from ai_gateway.core.config import AuthSettings, IssuerSettings
from ai_gateway.core.errors import PermissionDeniedError, UnauthenticatedError
from ai_gateway.core.security import TokenVerifier

ISSUER = "https://auth.idp.local/realms/idp"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class _Key:
    key = KEY.public_key()


class _Jwks:
    def get_signing_key_from_jwt(self, token: str) -> _Key:
        return _Key()


@pytest.fixture
def verifier() -> TokenVerifier:
    issuer = IssuerSettings.model_validate({"issuer": ISSUER, "jwks_url": f"{ISSUER}/certs"})
    verifier = TokenVerifier(AuthSettings(issuers=[issuer]))
    verifier._clients = {ISSUER: _Jwks()}  # type: ignore[dict-item]  # a stand-in JWKS client, no network
    return verifier


def token(**changes: object) -> str:
    claims = {"iss": ISSUER, "aud": "ai-gateway", "azp": "extraction-service", "exp": int(time.time()) + 300}
    claims.update(changes)
    return jwt.encode({k: v for k, v in claims.items() if v is not None}, KEY, algorithm="RS256")


async def test_returns_the_calling_client_when_the_token_is_valid(verifier: TokenVerifier) -> None:
    assert (await verifier.verify(token())).client_id == "extraction-service"


@pytest.mark.parametrize(
    "changes",
    [{"aud": "idp-api"}, {"iss": "https://evil.example/realms/x"}, {"exp": int(time.time()) - 600}, {"exp": None}],
)
async def test_refuses_with_401_when_the_token_is_not_for_this_service(
    verifier: TokenVerifier, changes: dict[str, object]
) -> None:
    with pytest.raises(UnauthenticatedError):
        await verifier.verify(token(**changes))


async def test_refuses_with_401_when_the_token_is_signed_with_a_shared_secret(verifier: TokenVerifier) -> None:
    forged = jwt.encode({"iss": ISSUER, "aud": "ai-gateway", "exp": int(time.time()) + 60}, "s" * 32, "HS256")
    with pytest.raises(UnauthenticatedError):
        await verifier.verify(forged)


async def test_refuses_with_403_when_the_client_is_not_allowed(verifier: TokenVerifier) -> None:
    with pytest.raises(PermissionDeniedError):
        await verifier.verify(token(azp="web-app"))
