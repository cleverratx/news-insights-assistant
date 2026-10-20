from dataclasses import dataclass
from typing import Protocol
from uuid import uuid4

from redis.exceptions import RedisError

from app.llm.errors import BudgetExceededError, BudgetUnavailableError

RESERVE_SCRIPT = """
local spent = tonumber(redis.call('HGET', KEYS[1], 'spent') or '0')
local reserved = tonumber(redis.call('HGET', KEYS[1], 'reserved') or '0')
if redis.call('HGET', KEYS[1], 'blocked') == '1' then return 0 end
if spent + reserved + tonumber(ARGV[2]) > tonumber(ARGV[3]) then return 0 end
redis.call('HSET', KEYS[1], ARGV[1], ARGV[2])
redis.call('HINCRBY', KEYS[1], 'reserved', ARGV[2])
redis.call('EXPIRE', KEYS[1], 604800)
return 1
"""

SETTLE_SCRIPT = """
local amount = redis.call('HGET', KEYS[1], ARGV[1])
if not amount then return 0 end
redis.call('HDEL', KEYS[1], ARGV[1])
redis.call('HINCRBY', KEYS[1], 'reserved', -tonumber(amount))
redis.call('HINCRBY', KEYS[1], 'spent', ARGV[2])
if tonumber(ARGV[2]) > tonumber(amount) then
    redis.call('HSET', KEYS[1], 'blocked', '1')
end
return 1
"""


@dataclass(frozen=True)
class Reservation:
    day: str
    token: str
    amount: int


class Budget(Protocol):
    async def reserve(self, day: str, amount: int, limit: int) -> Reservation: ...

    async def settle(self, reservation: Reservation, actual: int) -> None: ...


class RedisExecutor(Protocol):
    async def execute_command(self, *args: str | int) -> object: ...


class RedisBudget:
    def __init__(self, redis: RedisExecutor, namespace: str = "llm:budget") -> None:
        self.redis = redis
        self.namespace = namespace

    async def reserve(self, day: str, amount: int, limit: int) -> Reservation:
        if amount < 0 or limit <= 0:
            raise ValueError("Некорректная сумма резервирования LLM")
        reservation = Reservation(day, f"request:{uuid4().hex}", amount)
        try:
            accepted = await self.redis.execute_command(
                "EVAL",
                RESERVE_SCRIPT,
                1,
                f"{self.namespace}:{day}",
                reservation.token,
                amount,
                limit,
            )
        except RedisError:
            raise BudgetUnavailableError("Ошибка резервирования бюджета LLM") from None
        if not accepted:
            raise BudgetExceededError("Дневной бюджет LLM исчерпан или заблокирован")
        return reservation

    async def settle(self, reservation: Reservation, actual: int) -> None:
        if actual < 0:
            raise ValueError("Некорректная сумма расхода LLM")
        try:
            settled = await self.redis.execute_command(
                "EVAL",
                SETTLE_SCRIPT,
                1,
                f"{self.namespace}:{reservation.day}",
                reservation.token,
                actual,
            )
        except RedisError:
            raise BudgetUnavailableError("Ошибка учёта расхода LLM; резерв сохранён") from None
        if not settled:
            raise BudgetUnavailableError("Резерв бюджета LLM не найден")
