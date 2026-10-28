"""Bearer tokens (SEC-01): signature, issuer, audience and expiry checked; roles become permissions."""

import time
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from extraction.core.config import AuthSettings, IssuerSettings
from extraction.core.errors import UnauthenticatedError
from extraction.core.permissions import STANDARD_TEMPLATE_WRITE, TEMPLATE_PUBLISH, TEMPLATE_READ, TEMPLATE_WRITE
from extraction.core.security import TokenVerifier, principal_from_claims

ISSUER = "https://auth.idp.local/realms/idp"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
ACCOUNT = uuid4()


class _Key:
    key = KEY.public_key()


class _Jwks:
    def get_signing_key_from_jwt(self, token: str) -> _Key:
        return _Key()


@pytest.fixture
def verifier() -> TokenVerifier:
    settings = AuthSettings(issuers=[IssuerSettings(issuer=ISSUER, jwks_url=f"{ISSUER}/certs")])  # type: ignore[arg-type]
    verifier = TokenVerifier(settings)
    verifier._clients = {ISSUER: _Jwks()}
    return verifier


def _token(**changes: object) -> str:
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": "idp-api",
        "sub": "user-1",
        "exp": now + 300,
        "nbf": now - 5,
        "account_id": str(ACCOUNT),
        "roles": ["ORG_ADMIN"],
    }
    claims.update(changes)
    return jwt.encode({k: v for k, v in claims.items() if v is not None}, KEY, algorithm="RS256")


async def test_returns_the_principal_with_permissions_from_the_roles(verifier: TokenVerifier) -> None:
    principal = await verifier.verify(_token())

    assert (principal.actor_id, principal.tenant_id, principal.kind) == ("user-1", ACCOUNT, "user")
    assert {TEMPLATE_READ, TEMPLATE_WRITE, TEMPLATE_PUBLISH} <= principal.permissions
    assert not principal.is_platform_admin


@pytest.mark.parametrize(
    "token",
    [
        lambda: _token(aud="another-api"),
        lambda: _token(iss="https://evil.example/realms/x"),
        lambda: _token(exp=int(time.time()) - 600),
        lambda: _token(sub=None),
        lambda: _token(account_id="not-a-uuid"),
        lambda: jwt.encode(
            {"iss": ISSUER, "aud": "idp-api", "sub": "x", "exp": int(time.time()) + 60}, "secret", algorithm="HS256"
        ),
        lambda: "not.a.token",
    ],
)
async def test_refuses_with_401_when_the_token_is_not_valid_for_this_api(verifier: TokenVerifier, token) -> None:
    with pytest.raises(UnauthenticatedError):
        await verifier.verify(token())


def test_gives_platform_staff_the_standard_template_permission_and_api_keys_their_scopes() -> None:
    staff = principal_from_claims({"sub": "s", "realm_access": {"roles": ["PLATFORM_ADMIN"]}})
    key = principal_from_claims({"sub": "k", "kind": "api_key", "account_id": str(ACCOUNT), "scope": "templates:write"})

    assert staff.is_platform_admin
    assert STANDARD_TEMPLATE_WRITE in staff.permissions
    assert key.kind == "api_key"
    assert key.permissions == {TEMPLATE_READ, TEMPLATE_WRITE}  # API keys can't publish
