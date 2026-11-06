"""Bearer token verification and the principal (SEC-01 to SEC-05; IDP-01 ch. 2 and 4.4).

Every token is checked against its issuer's JWKS (cached), with ``iss`` from an allow-list, ``aud``, ``exp`` and
``nbf``, and only RS256/ES256 signatures. The tenant is the ``account_id`` claim, never a header or the body.
"""

from collections.abc import Mapping, Sequence
from typing import Any, Literal, NamedTuple
from uuid import UUID

import jwt
from fastapi import Request
from fastapi.security import HTTPBearer
from starlette.concurrency import run_in_threadpool

from conversion.core.config import AuthSettings
from conversion.core.errors import UnauthenticatedError
from conversion.core.permissions import permissions_for

ALGORITHMS = ("RS256", "ES256")
_KINDS: dict[str, Literal["user", "service", "api_key"]] = {"user": "user", "service": "service", "api_key": "api_key"}
bearer = HTTPBearer(auto_error=False)


class Principal(NamedTuple):
    actor_id: str  # the token's sub
    tenant_id: UUID | None  # account_id; None for platform staff and users still onboarding
    permissions: frozenset[str]
    kind: Literal["user", "service", "api_key", "anonymous"]


class TokenVerifier:
    """Verifies tokens from the configured issuers. JWKS clients are created once and cache their keys."""

    def __init__(self, settings: AuthSettings) -> None:
        self._settings = settings
        lifespan = int(settings.jwks_cache.total_seconds())
        self._clients = {
            str(issuer.issuer): jwt.PyJWKClient(str(issuer.jwks_url), cache_keys=True, lifespan=lifespan)
            for issuer in settings.issuers
        }

    async def verify(self, token: str) -> Principal:
        return await run_in_threadpool(self._verify, token)  # JWKS fetch and crypto are blocking (ASY-01)

    def _verify(self, token: str) -> Principal:
        try:
            issuer = jwt.decode(token, options={"verify_signature": False}).get("iss")  # only to pick the JWKS
            client = self._clients.get(str(issuer))
            if client is None:
                raise UnauthenticatedError
            key = client.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                key.key,
                algorithms=list(ALGORITHMS),
                audience=self._settings.audience,
                issuer=str(issuer),
                leeway=self._settings.leeway.total_seconds(),
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except (jwt.PyJWTError, ValueError) as exc:
            raise UnauthenticatedError from exc
        return principal_from_claims(claims)


def principal_from_claims(claims: Mapping[str, Any]) -> Principal:
    roles = [*_strings(claims.get("roles")), *_strings((claims.get("realm_access") or {}).get("roles"))]
    scopes = str(claims.get("scope") or "").split()
    account = claims.get("account_id")
    kind = _KINDS.get(str(claims.get("kind")), "user")
    try:
        tenant = UUID(str(account)) if account else None
    except ValueError:
        raise UnauthenticatedError from None
    return Principal(
        actor_id=str(claims["sub"]),
        tenant_id=tenant,
        permissions=permissions_for(roles, scopes),
        kind=kind,
    )


def _strings(value: object) -> Sequence[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def provide_token_verifier(request: Request) -> TokenVerifier:
    verifier: TokenVerifier = request.app.state.token_verifier  # built once in create_app (DI-03)
    return verifier
