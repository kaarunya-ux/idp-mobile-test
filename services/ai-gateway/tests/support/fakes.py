"""Fakes for the gateway's ports, shared by the unit tests (made-up values only)."""

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from uuid import UUID

from ai_gateway.ai.application.runner import ModelCallRunner
from ai_gateway.ai.domain.exceptions import CapReachedError
from ai_gateway.ai.domain.models import (
    Caller,
    Image,
    ImageRef,
    ModelReply,
    ModelRequest,
    ModelRole,
    Plan,
    Priority,
    Task,
    Tokens,
)
from ai_gateway.ai.infrastructure.prompt_files import PromptFiles

ACCOUNT = UUID("0192f0aa-0000-7000-8000-000000000001")
NOW = datetime(2026, 10, 28, 9, 30, tzinfo=UTC)


class FixedClock:
    def now(self) -> datetime:
        return NOW


class FakeModel:
    """Answers with the given texts in order; an exception in the list is raised instead."""

    def __init__(self, *answers: str | Exception) -> None:
        self.answers = list(answers)
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest, *, timeout_s: float) -> ModelReply:
        self.requests.append(request)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return ModelReply(model=f"{request.role.value}-test", content=answer, tokens=Tokens(input=100, output=10))


class FakeSlots:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.taken: list[tuple[ModelRole, Priority]] = []

    @asynccontextmanager
    async def slot(self, role: ModelRole, priority: Priority) -> AsyncIterator[None]:
        if self.error is not None:
            raise self.error
        self.taken.append((role, priority))
        yield


class FakeCaps:
    def __init__(self, left: int = 10) -> None:
        self.left = left
        self.refunds = 0

    async def reserve(self, account_id: str, plan: Plan, day: date) -> None:
        if self.left <= 0:
            raise CapReachedError
        self.left -= 1

    async def refund(self, account_id: str, day: date) -> None:
        self.left += 1
        self.refunds += 1


class FakeImages:
    def __init__(self) -> None:
        self.loaded: list[tuple[list[ImageRef], bool]] = []

    async def load(self, refs: Sequence[ImageRef], *, stack: bool) -> Sequence[Image]:
        self.loaded.append((list(refs), stack))
        count = 1 if stack else len(refs)
        return [Image(data=b"jpeg") for _ in range(count)]


class FakeMetrics:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def call_finished(self, *, task: str, model: str, outcome: str, latency_s: float | None, tokens: Tokens) -> None:
        self.calls.append((task, model, outcome))

    def waited(self, *, priority: Priority, seconds: float) -> None:
        return None


def caller(priority: Priority = Priority.STANDARD, plan: Plan = Plan.PERSONAL) -> Caller:
    return Caller(client_id="extraction-service", account_id=ACCOUNT, priority=priority, plan=plan)


def runner(
    model: FakeModel,
    *,
    caps: FakeCaps | None = None,
    slots: FakeSlots | None = None,
    metrics: FakeMetrics | None = None,
) -> ModelCallRunner:
    return ModelCallRunner(
        model=model,
        slots=slots or FakeSlots(),
        caps=caps or FakeCaps(),
        prompts=PromptFiles.load(),
        metrics=metrics or FakeMetrics(),
        clock=FixedClock(),
        timeouts=dict.fromkeys(Task, 30.0),
    )
