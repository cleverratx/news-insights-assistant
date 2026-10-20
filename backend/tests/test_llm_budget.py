import asyncio
import os
from collections.abc import AsyncIterator
from unittest.mock import patch
from uuid import uuid4

import pytest
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError

from app.llm.budget import MAX_ACCOUNTING_UNITS, RedisBudget, Reservation
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
    await budget.settle(reservation, 40)
    with pytest.raises(BudgetUnavailableError):
        await budget.settle(reservation, 41)
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


async def test_replayed_reserve_does_not_leak_budget(ledger: tuple[RedisBudget, Redis]) -> None:
    budget, redis = ledger
    with patch("app.llm.budget.uuid4", return_value=uuid4()):
        first = await budget.reserve("2026-10-20", 100, 500)
        second = await budget.reserve("2026-10-20", 100, 500)
    assert first == second
    assert await redis.hget(f"{budget.namespace}:2026-10-20", "reserved") == b"100"
    await budget.settle(first, 40)
    assert await redis.hget(f"{budget.namespace}:2026-10-20", "reserved") == b"0"


async def test_redis_command_replay_after_lost_reply(ledger: tuple[RedisBudget, Redis]) -> None:
    budget, redis = ledger

    class ReplayRedis:
        async def execute_command(self, *args: str | int) -> object:
            await redis.execute_command(*args)
            return await redis.execute_command(*args)

    replayed = RedisBudget(ReplayRedis(), budget.namespace)
    reservation = await replayed.reserve("2026-10-20", 100, 500)
    await replayed.settle(reservation, 40)
    key = f"{budget.namespace}:2026-10-20"
    assert await redis.hget(key, "reserved") == b"0"
    assert await redis.hget(key, "spent") == b"40"


async def test_conflicting_reserve_cannot_change_held_amount(
    ledger: tuple[RedisBudget, Redis],
) -> None:
    budget, redis = ledger
    with patch("app.llm.budget.uuid4", return_value=uuid4()):
        reservation = await budget.reserve("2026-10-20", 100, 500)
        with pytest.raises(BudgetUnavailableError):
            await budget.reserve("2026-10-20", 200, 500)
        await budget.settle(reservation, 40)
        with pytest.raises(BudgetUnavailableError):
            await budget.reserve("2026-10-20", 100, 500)
    assert await redis.hget(f"{budget.namespace}:2026-10-20", "reserved") == b"0"


@pytest.mark.parametrize("field,value", [("spent", "invalid"), ("reserved", "0")])
async def test_corrupt_state_preserves_reservation(
    ledger: tuple[RedisBudget, Redis], field: str, value: str
) -> None:
    budget, redis = ledger
    reservation = await budget.reserve("2026-10-20", 100, 500)
    key = f"{budget.namespace}:2026-10-20"
    await redis.hset(key, field, value)
    before = await redis.hgetall(key)
    with pytest.raises(BudgetUnavailableError):
        await budget.settle(reservation, 40)
    assert await redis.hgetall(key) == before


@pytest.mark.parametrize("amount,limit", [(-1, 500), (100, 0), (MAX_ACCOUNTING_UNITS + 1, 500)])
async def test_invalid_amount_never_reaches_redis(amount: int, limit: int) -> None:
    class NeverCalledRedis:
        async def execute_command(self, *args: str | int) -> object:
            pytest.fail("Некорректная сумма должна проверяться до Redis")

    budget = RedisBudget(NeverCalledRedis())
    with pytest.raises(ValueError):
        await budget.reserve("2026-10-20", amount, limit)


async def test_missing_reservation_does_not_create_ledger(
    ledger: tuple[RedisBudget, Redis],
) -> None:
    budget, redis = ledger
    with pytest.raises(BudgetUnavailableError):
        await budget.settle(Reservation("2026-10-20", "missing", 100), 40)
    assert not await redis.exists(f"{budget.namespace}:2026-10-20")
