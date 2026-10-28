"""Service token verification (SEC-01, SEC-07; IDP-03 ch. 4.2: "service token required").

Only client-credentials tokens are accepted: signature against the issuer's JWKS (cached), ``iss`` from an
allow-list, ``aud`` = ``ai-gateway``, ``exp``, RS256/ES256 only, and an ``azp`` (the calling client) on the allowed
list. User tokens are refused: the gateway is internal, and callers' accounts travel in the body, checked against caps.
"""

from typing import Annotated, NamedTuple

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.concurrency import run_in_threadpool

from ai_gateway.core.config import AuthSettings
from ai_gateway.core.errors import PermissionDeniedError, UnauthenticatedError

ALGORITHMS = ("RS256", "ES256")
bearer = HTTPBearer(auto_error=False)


class ServicePrincipal(NamedTuple):
    client_id: str  # the token's azp: extraction-service or conversion-service


class TokenVerifier:
    def __init__(self, settings: AuthSettings) -> None:
        self._settings = settings
        lifespan = int(settings.jwks_cache.total_seconds())
        self._clients = {
            str(issuer.issuer): jwt.PyJWKClient(str(issuer.jwks_url), cache_keys=True, lifespan=lifespan)
            for issuer in settings.issuers
        }

    async def verify(self, token: str) -> ServicePrincipal:
        return await run_in_threadpool(self._verify, token)  # JWKS fetch and crypto are blocking (ASY-01)

    def _verify(self, token: str) -> ServicePrincipal:
        try:
            issuer = jwt.decode(token, options={"verify_signature": False}).get("iss")
            client = self._clients.get(str(issuer))
            if client is None:
                raise UnauthenticatedError
            claims = jwt.decode(
                token,
                client.get_signing_key_from_jwt(token).key,
                algorithms=list(ALGORITHMS),
                audience=self._settings.audience,
                issuer=str(issuer),
                leeway=self._settings.leeway.total_seconds(),
                options={"require": ["exp", "iss", "aud"]},
            )
        except (jwt.PyJWTError, ValueError) as exc:
            raise UnauthenticatedError from exc
        caller = str(claims.get("azp") or claims.get("client_id") or "")
        if caller not in self._settings.allowed_clients:
            raise PermissionDeniedError("service:ai-gateway")
        return ServicePrincipal(client_id=caller)


def provide_token_verifier(request: Request) -> TokenVerifier:
    verifier: TokenVerifier = request.app.state.token_verifier
    return verifier


async def current_service(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    verifier: Annotated[TokenVerifier, Depends(provide_token_verifier)],
) -> ServicePrincipal:
    if credentials is None:
        raise UnauthenticatedError
    return await verifier.verify(credentials.credentials)
