"""One model call, the same way for every task (IDP-03 ch. 4.3 and 4.5; LLM-03, LLM-05, LLM-06).

Order: the account's daily cap, then a slot on the shared model semaphore (fair queues), then the call, then the
answer is checked. An answer that isn't the requested shape gets one repair attempt, then ``OutputInvalidError``.
A reserved call that never reached the model (busy, model down) is given back to the account.

Only ids, sizes, token counts, latency and outcome are logged: never a prompt or document content (LLM-06).
"""

import time
from collections.abc import Callable, Mapping
from dataclasses import replace

import structlog

from ai_gateway.ai.domain.exceptions import AnswerShapeError, OutputInvalidError
from ai_gateway.ai.domain.models import Answer, Caller, ModelReply, ModelRequest, Prompt, Provenance, Task, Tokens
from ai_gateway.ai.domain.ports import CapsPort, MetricsPort, ModelPort, PromptsPort, SlotsPort
from ai_gateway.core.clock import Clock
from ai_gateway.core.errors import DomainError

log = structlog.get_logger(__name__)

REPAIR_PROMPT = "repair"
_PROBLEM_LENGTH = 200


class ModelCallRunner:
    # every collaborator is a port or a number
    def __init__(  # noqa: PLR0913
        self,
        *,
        model: ModelPort,
        slots: SlotsPort,
        caps: CapsPort,
        prompts: PromptsPort,
        metrics: MetricsPort,
        clock: Clock,
        timeouts: Mapping[Task, float],
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._model = model
        self._slots = slots
        self._caps = caps
        self._prompts = prompts
        self._metrics = metrics
        self._clock = clock
        self._timeouts = timeouts
        self._monotonic = monotonic

    async def run[T](
        self, caller: Caller, task: Task, prompt: Prompt, request: ModelRequest, read: Callable[[str], T]
    ) -> Answer[T]:
        """``read`` turns the answer text into the task's value, raising ``AnswerShapeError`` when it can't."""
        day = self._clock.now().date()
        await self._caps.reserve(str(caller.account_id), caller.plan, day)
        answered = False
        queued = self._monotonic()
        try:
            async with self._slots.slot(request.role, caller.priority):
                started = self._monotonic()
                self._metrics.waited(priority=caller.priority, seconds=started - queued)
                reply = await self._model.complete(request, timeout_s=self._timeouts[task])
                answered = True
                value, tokens, model = await self._read_or_repair(task, request, reply, read)
        except DomainError as error:
            if not answered:
                await self._caps.refund(str(caller.account_id), day)
            self._metrics.call_finished(
                task=task, model=request.role.value, outcome=error.code.value, latency_s=None, tokens=Tokens()
            )
            self._finished(caller, task, prompt, outcome=error.code.value)
            raise
        latency_s = self._monotonic() - started
        provenance = Provenance(
            model=model, prompt_version=prompt.label, latency_ms=round(latency_s * 1000), tokens=tokens
        )
        self._metrics.call_finished(task=task, model=model, outcome="OK", latency_s=latency_s, tokens=tokens)
        self._finished(caller, task, prompt, outcome="OK", latency_ms=provenance.latency_ms, tokens=tokens)
        return Answer(value=value, provenance=provenance)

    async def _read_or_repair[T](
        self, task: Task, request: ModelRequest, reply: ModelReply, read: Callable[[str], T]
    ) -> tuple[T, Tokens, str]:
        try:
            return read(reply.content), reply.tokens, reply.model
        except AnswerShapeError as first:
            problem = str(first)[:_PROBLEM_LENGTH]
        repair = self._prompts.get(REPAIR_PROMPT).system.replace("{problem}", problem)
        retry = replace(request, follow_up=(*request.follow_up, ("assistant", reply.content), ("user", repair)))
        second = await self._model.complete(retry, timeout_s=self._timeouts[task])
        try:
            return read(second.content), reply.tokens + second.tokens, second.model
        except AnswerShapeError as exc:
            raise OutputInvalidError from exc

    def _finished(self, caller: Caller, task: Task, prompt: Prompt, *, outcome: str, **numbers: object) -> None:
        log.info(
            "ai_call_finished",
            task=task.value,
            prompt_version=prompt.label,
            client=caller.client_id,
            account_id=str(caller.account_id),
            priority=caller.priority.value,
            outcome=outcome,
            **numbers,
        )
