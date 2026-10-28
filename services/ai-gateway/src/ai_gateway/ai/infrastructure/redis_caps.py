"""``CapsPort`` on Redis counters: AI calls per account per UTC day (IDP-03 ch. 4.5).

Key ``aig:cap:<accountId>:<yyyy-mm-dd>`` (RDS-01), kept two days (RDS-02). If Redis is down the cap fails open and
logs an error for the alert (RDS-07: a rate limit), because the semaphore, which fails closed, still protects the
model server. Credits and plan limits are user-service's business; these are the gateway's ceilings.
"""

from collections.abc import Mapping
from datetime import date, timedelta

import structlog
from redis.asyncio import Redis
from redis.exceptions import RedisError

from ai_gateway.ai.domain.exceptions import CapReachedError
from ai_gateway.ai.domain.models import Plan

log = structlog.get_logger(__name__)

_KEEP = timedelta(days=2)


class RedisCaps:
    def __init__(self, redis: Redis, *, limits: Mapping[Plan, int]) -> None:
        self._redis = redis
        self._limits = limits

    async def reserve(self, account_id: str, plan: Plan, day: date) -> None:
        key = _key(account_id, day)
        try:
            count = int(await self._redis.incr(key))
            if count == 1:
                await self._redis.expire(key, _KEEP)
            if count <= self._limits[plan]:
                return
            await self._redis.decr(key)
        except RedisError as exc:
            log.error("ai_caps_unavailable", error=type(exc).__name__)  # fails open
            return
        raise CapReachedError

    async def refund(self, account_id: str, day: date) -> None:
        try:
            await self._redis.decr(_key(account_id, day))
        except RedisError as exc:
            log.warning("ai_caps_refund_failed", error=type(exc).__name__)


def _key(account_id: str, day: date) -> str:
    return f"aig:cap:{account_id}:{day.isoformat()}"
