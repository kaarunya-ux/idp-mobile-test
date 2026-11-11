"""The worker processes of the ``conversion-sync`` process (ADR 0002; standard ch. 15: a process for CPU-bound Python).

Each conversion runs in a worker process, as in a Celery prefork worker: PDFium can't be used from two threads at once,
a page's work holds the GIL, and a crash in a native library takes one process down rather than the server. They are
spawned, not forked: the server already runs threads and an event loop.
"""

import asyncio
import multiprocessing
import time
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from functools import lru_cache

import structlog

from conversion.bootstrap import build_convert_now_service
from conversion.conversion.application.services.convert_now import (
    ConvertNowCommand,
    ConvertNowResult,
    ConvertNowService,
    MergeNowCommand,
)
from conversion.conversion.domain.exceptions import UnexpectedFailureError
from conversion.core.build_info import build_stamp
from conversion.core.config import get_settings
from conversion.core.errors import DomainError
from conversion.core.logging import CORRELATION_ID_KEY, configure_logging, get_correlation_id

log = structlog.get_logger(__name__)

_WARM_UP_HOLD_S = 1.0  # long enough that every warm-up task lands on its own process


@lru_cache(maxsize=1)
def _service() -> ConvertNowService:
    return build_convert_now_service(get_settings())


def _start_worker() -> None:
    settings = get_settings()
    configure_logging(settings.logging, build_stamp(settings))


def _warm_up() -> None:
    _service()
    time.sleep(_WARM_UP_HOLD_S)


def _convert(command: ConvertNowCommand | MergeNowCommand, correlation_id: str | None) -> ConvertNowResult:
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(**{CORRELATION_ID_KEY: correlation_id})
    try:
        service = _service()
        return service.merge(command) if isinstance(command, MergeNowCommand) else service.convert(command)
    except DomainError:
        raise
    except Exception as exc:  # the trace stays in the log (ERR-06), and nothing unpicklable reaches the pool
        log.error("conversion_failed_unexpectedly", error_type=type(exc).__name__, exc_info=exc)
        raise UnexpectedFailureError from exc


class ProcessConversions:
    """``ConvertNowRunner`` on a pool of worker processes. A process that dies breaks the pool, so the pool is
    replaced: that request fails and the next ones are served."""

    def __init__(self, workers: int) -> None:
        self._workers = workers
        self._pool = self._new_pool()

    def _new_pool(self) -> ProcessPoolExecutor:
        return ProcessPoolExecutor(
            max_workers=self._workers, mp_context=multiprocessing.get_context("spawn"), initializer=_start_worker
        )

    async def warm_up(self) -> None:
        """Start every process now, so the first requests don't wait for its imports."""
        loop = asyncio.get_running_loop()
        await asyncio.gather(*(loop.run_in_executor(self._pool, _warm_up) for _ in range(self._workers)))

    async def convert(self, command: ConvertNowCommand) -> ConvertNowResult:
        return await self._run(command)

    async def merge(self, command: MergeNowCommand) -> ConvertNowResult:
        return await self._run(command)

    async def _run(self, command: ConvertNowCommand | MergeNowCommand) -> ConvertNowResult:
        pool = self._pool
        try:
            return await asyncio.get_running_loop().run_in_executor(pool, _convert, command, get_correlation_id())
        except BrokenProcessPool as exc:
            log.error("conversion_worker_died")
            if self._pool is pool:
                pool.shutdown(wait=False)
                self._pool = self._new_pool()
            raise UnexpectedFailureError from exc

    def close(self) -> None:
        self._pool.shutdown(wait=True, cancel_futures=True)
