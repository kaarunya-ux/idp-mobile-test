"""Service tokens for calls to other internal services (SEC-07): OAuth2 client credentials against Keycloak.

The token is cached until shortly before it expires; a 401 from the other service drops it so the next call fetches
a new one. Sync, for the Celery workers (standard ch. 13.1: sync end to end).
"""

import threading
import time
from collections.abc import Callable

import httpx
import structlog

from extraction.core.config import ServiceAuthSettings

log = structlog.get_logger(__name__)

_EARLY_S = 30  # renew this long before expiry


class ServiceTokenError(RuntimeError):
    """No token could be fetched."""


class ServiceTokenProvider:
    def __init__(
        self, client: httpx.Client, settings: ServiceAuthSettings, monotonic: Callable[[], float] = time.monotonic
    ) -> None:
        self._client = client
        self._settings = settings
        self._monotonic = monotonic
        self._lock = threading.Lock()
        self._token: str | None = None
        self._expires_at = 0.0

    def token(self) -> str:
        with self._lock:
            if self._token is None or self._monotonic() >= self._expires_at:
                self._token, self._expires_at = self._fetch()
            return self._token

    def invalidate(self) -> None:
        with self._lock:
            self._token = None

    def _fetch(self) -> tuple[str, float]:
        form = {
            "grant_type": "client_credentials",
            "client_id": self._settings.client_id,
            "client_secret": self._settings.client_secret.get_secret_value(),
        }
        if self._settings.audience:
            form["audience"] = self._settings.audience
        try:
            response = self._client.post(str(self._settings.token_url), data=form)
            response.raise_for_status()
            body = response.json()
            token, lifetime = str(body["access_token"]), float(body.get("expires_in", 60))
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            log.warning("service_token_failed", error=type(exc).__name__)
            raise ServiceTokenError("no service token") from exc
        return token, self._monotonic() + max(lifetime - _EARLY_S, 1.0)
