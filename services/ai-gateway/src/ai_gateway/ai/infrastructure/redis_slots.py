"""``SlotsPort`` on Redis: one semaphore per model, shared by every gateway replica, with three fair waiting queues
(IDP-03 ch. 4.5; the INTERACTIVE lane is ADR 0003).

A caller takes a ticket and joins its queue (INTERACTIVE, PRIORITY or STANDARD), then polls. One Lua script does each
poll atomically: it drops holders whose lease ran out (a crashed replica) and waiters that stopped polling, and grants
a free slot to the head of the queue whose turn it is. INTERACTIVE (a person waiting on an open request) always goes
first. The two queued lanes may together hold only ``capacity - interactive_reserved`` slots, so a batch of queued
jobs never takes the slots a person is waiting for; between them, PRIORITY is served ``share`` times for each STANDARD.
A caller that waits longer than its lane's wait timeout gets ``BusyError``. If Redis is down the call fails closed
(RDS-07: a lock), with 503.

All keys share the hash tag ``{aig:sem:global:<model>}`` so the script also works on a Redis cluster. The semaphore
is global, so its "tenant" part is ``global`` (RDS-01). Every key gets a TTL on each poll (RDS-02).
"""

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import structlog
from prometheus_client import Gauge
from redis.asyncio import Redis
from redis.exceptions import RedisError

from ai_gateway.ai.domain.exceptions import BusyError
from ai_gateway.ai.domain.models import ModelRole, Priority
from ai_gateway.core.errors import DependencyUnavailableError
from ai_gateway.core.ids import new_id

log = structlog.get_logger(__name__)

_LANES = {Priority.INTERACTIVE: "I", Priority.PRIORITY: "P", Priority.STANDARD: "S"}
IN_USE = Gauge("ai_semaphore_in_use", "Model slots held by gateway calls", ["model"])
_KEY_TTL_MS = 24 * 3600 * 1000
_ACQUIRE = """
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local holders, waitp, waits, alive, turnkey, seq = KEYS[1], KEYS[2], KEYS[3], KEYS[4], KEYS[5], KEYS[6]
local waiti, queued = KEYS[7], KEYS[8]
local ticket, mine = ARGV[1], ARGV[2]
local capacity, lease, alive_ms, share = tonumber(ARGV[3]), tonumber(ARGV[4]), tonumber(ARGV[5]), tonumber(ARGV[6])
local queued_capacity = tonumber(ARGV[8])
local myqueue = waits
if mine == 'P' then myqueue = waitp elseif mine == 'I' then myqueue = waiti end
if not redis.call('ZSCORE', myqueue, ticket) then
  redis.call('ZADD', myqueue, redis.call('INCR', seq), ticket)
end
redis.call('HSET', alive, ticket, now + alive_ms)
for _, key in ipairs(KEYS) do redis.call('PEXPIRE', key, ARGV[7]) end
redis.call('ZREMRANGEBYSCORE', holders, '-inf', now)
redis.call('ZREMRANGEBYSCORE', queued, '-inf', now)
local function head(queue)
  while true do
    local first = redis.call('ZRANGE', queue, 0, 0)
    if #first == 0 then return nil end
    if tonumber(redis.call('HGET', alive, first[1]) or '0') >= now then return first[1] end
    redis.call('ZREM', queue, first[1])
    redis.call('HDEL', alive, first[1])
  end
end
local free = capacity - redis.call('ZCARD', holders)
if free <= 0 then return 0 end
local hi = head(waiti)
if mine == 'I' then
  if hi ~= ticket then return 0 end
  redis.call('ZREM', waiti, ticket)
  redis.call('HDEL', alive, ticket)
  redis.call('ZADD', holders, now + lease, ticket)
  return 1
end
if hi and free <= redis.call('ZCARD', waiti) then return 0 end
if redis.call('ZCARD', queued) >= queued_capacity then return 0 end
local hp, hs = head(waitp), head(waits)
local turn = tonumber(redis.call('GET', turnkey) or '0')
local pick = 'S'
if hp and (not hs or turn < share) then pick = 'P' end
local chosen, queue = hs, waits
if pick == 'P' then chosen, queue = hp, waitp end
if chosen ~= ticket then return 0 end
redis.call('ZREM', queue, ticket)
redis.call('HDEL', alive, ticket)
redis.call('ZADD', holders, now + lease, ticket)
redis.call('ZADD', queued, now + lease, ticket)
local next_turn = 0
if pick == 'P' then next_turn = turn + 1 end
redis.call('SET', turnkey, next_turn, 'PX', ARGV[7])
return 1
"""


class RedisSlots:
    # every collaborator is a client or a number
    def __init__(  # noqa: PLR0913
        self,
        redis: Redis,
        *,
        capacity: int,
        wait_timeout_s: float,
        lease_s: float,
        priority_share: int,
        interactive_reserved: int = 0,
        interactive_wait_timeout_s: float | None = None,
        poll_s: float = 0.1,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if not 0 <= interactive_reserved < capacity:
            raise ValueError("interactive_reserved must leave at least one slot for the queued lanes")
        self._redis = redis
        self._capacity = capacity
        self._queued_capacity = capacity - interactive_reserved
        self._wait_timeout_s = {
            Priority.INTERACTIVE: interactive_wait_timeout_s or wait_timeout_s,
            Priority.PRIORITY: wait_timeout_s,
            Priority.STANDARD: wait_timeout_s,
        }
        self._lease_ms = int(lease_s * 1000)
        self._share = priority_share
        self._poll_s = poll_s
        self._alive_ms = max(1000, int(poll_s * 5000))  # a waiter that missed about five polls has gone
        self._monotonic = monotonic
        self._script = redis.register_script(_ACQUIRE)

    @asynccontextmanager
    async def slot(self, role: ModelRole, priority: Priority) -> AsyncIterator[None]:
        keys = _keys(role)
        ticket = str(new_id())
        await self._wait_for_slot(keys, ticket, priority)
        IN_USE.labels(model=role.value).inc()
        try:
            yield
        finally:
            IN_USE.labels(model=role.value).dec()
            await self._quietly(self._redis.zrem(keys[0], ticket))
            await self._quietly(self._redis.zrem(keys[7], ticket))

    async def _wait_for_slot(self, keys: list[str], ticket: str, priority: Priority) -> None:
        deadline = self._monotonic() + self._wait_timeout_s[priority]
        args: list[str | int] = [
            ticket,
            _LANES[priority],
            self._capacity,
            self._lease_ms,
            self._alive_ms,
            self._share,
            _KEY_TTL_MS,
            self._queued_capacity,
        ]
        try:
            while not await self._script(keys=keys, args=args):
                if self._monotonic() >= deadline:
                    raise BusyError
                await asyncio.sleep(self._poll_s)
        except RedisError as exc:
            log.error("model_queue_unavailable", error=type(exc).__name__)  # fails closed: no slot, no call
            await self._leave(keys, ticket)
            raise DependencyUnavailableError("model queue") from exc
        except BaseException:  # busy, or the request was cancelled: leave the queue
            await self._leave(keys, ticket)
            raise

    async def _leave(self, keys: list[str], ticket: str) -> None:
        await self._quietly(self._redis.zrem(keys[1], ticket))
        await self._quietly(self._redis.zrem(keys[2], ticket))
        await self._quietly(self._redis.zrem(keys[6], ticket))
        await self._quietly(self._redis.hdel(keys[3], ticket))

    @staticmethod
    async def _quietly(call: Awaitable[object]) -> None:
        """Clean-up that may fail: a lease or the alive mark runs out on its own."""
        try:
            await call
        except RedisError as exc:
            log.warning("model_queue_cleanup_failed", error=type(exc).__name__)


def _keys(role: ModelRole) -> list[str]:
    base = f"{{aig:sem:global:{role.value}}}"
    return [
        f"{base}:holders",
        f"{base}:wait:priority",
        f"{base}:wait:standard",
        f"{base}:alive",
        f"{base}:turn",
        f"{base}:seq",
        f"{base}:wait:interactive",
        f"{base}:holders:queued",  # the holders from the PRIORITY and STANDARD lanes, kept below their share
    ]
