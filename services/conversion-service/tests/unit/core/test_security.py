"""Bearer tokens (SEC-01): signature, issuer, audience and expiry checked; roles become the one permission."""

import time
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from conversion.core.config import AuthSettings, IssuerSettings
from conversion.core.errors import UnauthenticatedError
from conversion.core.permissions import DOCUMENT_WRITE
from conversion.core.security import TokenVerifier, principal_from_claims

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
    settings = AuthSettings(issuers=[IssuerSettings(issuer=ISSUER, jwks_url=f"{ISSUER}/certs")])  # type: ignore[arg-type]  # pydantic turns the text into a URL
    verifier = TokenVerifier(settings)
    verifier._clients = {ISSUER: _Jwks()}  # type: ignore[dict-item]  # a stand-in with the one method used
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


async def test_returns_the_principal_with_the_permission_when_the_token_is_valid(verifier: TokenVerifier) -> None:
    principal = await verifier.verify(_token())

    assert (principal.actor_id, principal.tenant_id, principal.kind) == ("user-1", ACCOUNT, "user")
    assert DOCUMENT_WRITE in principal.permissions


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
async def test_refuses_when_the_token_is_not_valid_for_this_api(verifier: TokenVerifier, token) -> None:
    with pytest.raises(UnauthenticatedError):
        await verifier.verify(token())


def test_lets_every_account_role_and_the_documents_scope_write_documents() -> None:
    for role in ("GUEST", "PERSONAL", "MEMBER", "REVIEWER", "DEVELOPER", "ORG_ADMIN", "ORG_OWNER"):
        assert DOCUMENT_WRITE in principal_from_claims({"sub": "u", "roles": [role]}).permissions
    assert (
        DOCUMENT_WRITE in principal_from_claims({"sub": "k", "kind": "api_key", "scope": "documents:write"}).permissions
    )


def test_gives_no_permission_to_platform_staff_or_an_unknown_role() -> None:
    assert not principal_from_claims({"sub": "s", "realm_access": {"roles": ["PLATFORM_ADMIN"]}}).permissions
    assert not principal_from_claims({"sub": "u", "roles": ["SOMETHING_ELSE"]}).permissions
