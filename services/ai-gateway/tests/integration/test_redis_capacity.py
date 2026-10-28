"""The shared model semaphore and the daily caps on a real Redis (TST-02): capacity, 3:1 fairness, timeouts,
leases of crashed holders, cap exhaustion."""

import asyncio
from collections.abc import AsyncIterator, Iterator
from datetime import date

import pytest
from redis.asyncio import Redis
from testcontainers.redis import RedisContainer

from ai_gateway.ai.domain.exceptions import BusyError, CapReachedError
from ai_gateway.ai.domain.models import ModelRole, Plan, Priority
from ai_gateway.ai.infrastructure.redis_caps import RedisCaps
from ai_gateway.ai.infrastructure.redis_slots import RedisSlots

pytestmark = pytest.mark.integration
DAY = date(2026, 10, 28)


@pytest.fixture(scope="module")
def redis_url() -> Iterator[str]:
    with RedisContainer("redis:7-alpine") as container:
        yield f"redis://{container.get_container_host_ip()}:{container.get_exposed_port(6379)}/0"


@pytest.fixture
async def redis(redis_url: str) -> AsyncIterator[Redis]:
    client = Redis.from_url(redis_url, decode_responses=True)
    await client.flushdb()
    yield client
    await client.aclose()


def _slots(redis: Redis, *, capacity: int = 1, wait_s: float = 5, lease_s: float = 60) -> RedisSlots:
    return RedisSlots(redis, capacity=capacity, wait_timeout_s=wait_s, lease_s=lease_s, priority_share=3, poll_s=0.01)


async def test_never_holds_more_slots_than_the_capacity_when_many_calls_arrive(redis: Redis) -> None:
    slots, held, peak = _slots(redis, capacity=2), [0], [0]

    async def call() -> None:
        async with slots.slot(ModelRole.VL, Priority.STANDARD):
            held[0] += 1
            peak[0] = max(peak[0], held[0])
            await asyncio.sleep(0.02)
            held[0] -= 1

    await asyncio.gather(*(call() for _ in range(10)))

    assert peak[0] == 2


async def test_serves_priority_three_to_one_over_standard_when_both_queues_wait(redis: Redis) -> None:
    slots, order = _slots(redis), []
    blocker = slots.slot(ModelRole.VL, Priority.STANDARD)
    await blocker.__aenter__()  # hold the only slot while the queues fill

    async def call(priority: Priority) -> None:
        async with slots.slot(ModelRole.VL, priority):
            order.append(priority)
            await asyncio.sleep(0.01)

    tasks = []
    for _ in range(4):
        tasks.append(asyncio.create_task(call(Priority.STANDARD)))
        tasks.append(asyncio.create_task(call(Priority.PRIORITY)))
        await asyncio.sleep(0.03)  # let each join its queue in turn
    await blocker.__aexit__(None, None, None)
    await asyncio.gather(*tasks)

    first_four = order[:4]
    assert first_four.count(Priority.PRIORITY) == 3
    assert order.count(Priority.STANDARD) == 4


async def test_raises_busy_and_leaves_the_queue_when_no_slot_frees_up(redis: Redis) -> None:
    slots = _slots(redis, wait_s=0.2)
    async with slots.slot(ModelRole.VL, Priority.STANDARD):
        with pytest.raises(BusyError):
            async with slots.slot(ModelRole.VL, Priority.STANDARD):
                pass

    assert await redis.zcard("{aig:sem:global:vl}:wait:standard") == 0


async def test_frees_a_slot_when_its_holder_crashed_and_the_lease_ran_out(redis: Redis) -> None:
    crashed = _slots(redis, lease_s=0.1)
    await crashed.slot(ModelRole.VL, Priority.STANDARD).__aenter__()  # never released

    async with _slots(redis, wait_s=2).slot(ModelRole.VL, Priority.STANDARD):
        pass


async def test_keeps_separate_semaphores_per_model(redis: Redis) -> None:
    slots = _slots(redis, wait_s=0.2)
    async with slots.slot(ModelRole.VL, Priority.STANDARD), slots.slot(ModelRole.AUTO, Priority.STANDARD):
        pass


async def test_refuses_the_call_after_the_cap_and_counts_refunds_when_the_allowance_is_used(redis: Redis) -> None:
    caps = RedisCaps(redis, limits={Plan.GUEST: 2, Plan.PERSONAL: 30, Plan.CORPORATE: 5000})
    await caps.reserve("acct-1", Plan.GUEST, DAY)
    await caps.reserve("acct-1", Plan.GUEST, DAY)

    with pytest.raises(CapReachedError):
        await caps.reserve("acct-1", Plan.GUEST, DAY)
    await caps.refund("acct-1", DAY)
    await caps.reserve("acct-1", Plan.GUEST, DAY)
    await caps.reserve("acct-2", Plan.GUEST, DAY)  # another account has its own count
    assert 0 < await redis.ttl(f"aig:cap:acct-1:{DAY.isoformat()}") <= 2 * 86400


async def test_fails_open_when_redis_is_down_for_caps() -> None:
    down = Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.2)
    caps = RedisCaps(down, limits={Plan.GUEST: 0, Plan.PERSONAL: 0, Plan.CORPORATE: 0})

    await caps.reserve("acct-1", Plan.GUEST, DAY)  # no exception: rate limits fail open (RDS-07)
    await down.aclose()
