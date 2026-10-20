import asyncio
import os
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError

from app.llm.budget import RedisBudget, Reservation
from app.llm.errors import BudgetExceededError, BudgetUnavailableError


@pytest.fixture
async def ledger() -> AsyncIterator[tuple[RedisBudget, Redis]]:
    url = os.environ.get("TEST_REDIS_URL")
    if not url:
        pytest.skip("Для интеграционных тестов нужен TEST_REDIS_URL тестового Redis")
    redis = Redis.from_url(url)
    namespace = f"llm:test:{uuid4().hex}"
    try:
        await redis.ping()
        yield RedisBudget(redis, namespace), redis
    finally:
        async for key in redis.scan_iter(match=f"{namespace}:*"):
            await redis.delete(key)
        await redis.aclose()


async def test_parallel_clients_cannot_over_reserve(ledger: tuple[RedisBudget, Redis]) -> None:
    first, redis = ledger
    second = RedisBudget(redis, first.namespace)
    outcomes = await asyncio.gather(
        *[(first if i % 2 else second).reserve("2026-10-20", 100, 500) for i in range(20)],
        return_exceptions=True,
    )
    reservations = [item for item in outcomes if isinstance(item, Reservation)]
    rejections = [item for item in outcomes if isinstance(item, BudgetExceededError)]
    assert len(reservations) == 5
    assert len(rejections) == 15

    await first.settle(reservations[0], 40)
    await second.settle(reservations[1], 40)
    await first.reserve("2026-10-20", 120, 500)
    with pytest.raises(BudgetExceededError):
        await second.reserve("2026-10-20", 1, 500)
    key = f"{first.namespace}:2026-10-20"
    assert await redis.hget(key, "spent") == b"80"
    assert await redis.hget(key, "reserved") == b"420"
    assert 0 < await redis.ttl(key) <= 604800


async def test_overrun_blocks_only_current_day(ledger: tuple[RedisBudget, Redis]) -> None:
    budget, redis = ledger
    reservation = await budget.reserve("2026-10-20", 10, 500)
    await budget.settle(reservation, 11)
    with pytest.raises(BudgetExceededError):
        await budget.reserve("2026-10-20", 1, 500)
    await budget.reserve("2026-10-21", 1, 500)
    assert await redis.hget(f"{budget.namespace}:2026-10-20", "spent") == b"11"


async def test_repeated_settlement_does_not_double_charge(
    ledger: tuple[RedisBudget, Redis],
) -> None:
    budget, redis = ledger
    reservation = await budget.reserve("2026-10-20", 100, 500)
    await budget.settle(reservation, 40)
    with pytest.raises(BudgetUnavailableError):
        await budget.settle(reservation, 40)
    assert await redis.hget(f"{budget.namespace}:2026-10-20", "spent") == b"40"
    assert await redis.hget(f"{budget.namespace}:2026-10-20", "reserved") == b"0"


async def test_reservations_survive_new_client(ledger: tuple[RedisBudget, Redis]) -> None:
    first, redis = ledger
    await first.reserve("2026-10-20", 500, 500)
    second = RedisBudget(redis, first.namespace)
    with pytest.raises(BudgetExceededError):
        await second.reserve("2026-10-20", 1, 500)


async def test_redis_errors_are_sanitized() -> None:
    class UnavailableRedis:
        async def execute_command(self, *args: str | int) -> object:
            raise RedisConnectionError("test-only-redis-password")

    budget = RedisBudget(UnavailableRedis())
    with pytest.raises(BudgetUnavailableError) as error:
        await budget.reserve("2026-10-20", 1, 500)
    assert "test-only-redis-password" not in str(error.value)
    assert error.value.__suppress_context__
