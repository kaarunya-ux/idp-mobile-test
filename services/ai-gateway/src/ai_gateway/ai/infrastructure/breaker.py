"""An in-process circuit breaker for the model server (IDP-03 ch. 4.5).

Closed: calls go through, failures are counted. ``failures`` failures within ``window`` open it: calls fail at once
with ``ModelUnavailableError``. After ``reset_after`` one probe call is let through (half-open): success closes the
breaker, failure opens it again. Each gateway replica has its own breaker; that is enough to stop hammering a server
that is down.
"""

import time
from collections import deque
from collections.abc import Callable
from enum import StrEnum

from ai_gateway.ai.domain.exceptions import ModelUnavailableError


class BreakerState(StrEnum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreaker:
    def __init__(
        self,
        *,
        failures: int,
        window_s: float,
        reset_after_s: float,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._limit = failures
        self._window_s = window_s
        self._reset_after_s = reset_after_s
        self._monotonic = monotonic
        self._failures: deque[float] = deque()
        self._opened_at: float | None = None
        self._probing = False

    @property
    def state(self) -> BreakerState:
        if self._opened_at is None:
            return BreakerState.CLOSED
        if self._probing or self._monotonic() - self._opened_at >= self._reset_after_s:
            return BreakerState.HALF_OPEN
        return BreakerState.OPEN

    def before_call(self) -> None:
        """Raises ``ModelUnavailableError`` while open, and for every call but the probe while half-open."""
        state = self.state
        if state is BreakerState.OPEN or (state is BreakerState.HALF_OPEN and self._probing):
            raise ModelUnavailableError
        if state is BreakerState.HALF_OPEN:
            self._probing = True

    def succeeded(self) -> None:
        self._opened_at, self._probing = None, False
        self._failures.clear()

    def failed(self) -> None:
        now = self._monotonic()
        if self._probing:  # the probe failed: open again for another reset period
            self._opened_at, self._probing = now, False
            return
        self._failures.append(now)
        while self._failures and now - self._failures[0] > self._window_s:
            self._failures.popleft()
        if len(self._failures) >= self._limit:
            self._opened_at = now
            self._failures.clear()
