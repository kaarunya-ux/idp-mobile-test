"""What the application needs from the outside world. Adapters in ``infrastructure`` implement these."""

from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from datetime import date
from typing import Protocol

from ai_gateway.ai.domain.models import (
    Image,
    ImageRef,
    ModelReply,
    ModelRequest,
    ModelRole,
    Plan,
    Priority,
    Prompt,
    Tokens,
)


class ModelPort(Protocol):
    """The model server. Raises ``ModelUnavailableError`` when it can't answer (breaker open, timeout, 5xx)."""

    async def complete(self, request: ModelRequest, *, timeout_s: float) -> ModelReply: ...


class ImageStorePort(Protocol):
    """Page images by key. Raises ``InputInvalidError`` for a missing, foreign-bucket, unreadable or too large file."""

    async def load(self, refs: Sequence[ImageRef], *, stack: bool) -> Sequence[Image]:
        """The images, each resized to the long-side limit; ``stack`` puts two sides into one image."""
        ...


class SlotsPort(Protocol):
    """The model server's shared capacity, with fair waiting queues (IDP-03 ch. 4.5)."""

    def slot(self, role: ModelRole, priority: Priority) -> AbstractAsyncContextManager[None]:
        """Held while the model call runs. Raises ``BusyError`` after the wait timeout."""
        ...


class CapsPort(Protocol):
    """Daily AI calls per account."""

    async def reserve(self, account_id: str, plan: Plan, day: date) -> None:
        """Count one call. Raises ``CapReachedError`` when the account has none left today."""
        ...

    async def refund(self, account_id: str, day: date) -> None:
        """Give back a reserved call that never reached the model."""
        ...


class PromptsPort(Protocol):
    def get(self, name: str) -> Prompt:
        """The current version of a prompt."""
        ...


class MetricsPort(Protocol):
    """IDP-03 ch. 4.6: ``ai_requests_total``, ``ai_latency_seconds``, ``ai_tokens_total``, ``ai_queue_wait_seconds``."""

    def call_finished(
        self, *, task: str, model: str, outcome: str, latency_s: float | None, tokens: Tokens
    ) -> None: ...

    def waited(self, *, priority: Priority, seconds: float) -> None: ...
