from dataclasses import dataclass
from typing import Protocol
from uuid import uuid4

from redis.exceptions import RedisError

from app.llm.errors import BudgetExceededError, BudgetUnavailableError

MAX_ACCOUNTING_UNITS = 2**53 - 1

RESERVE_SCRIPT = """
local spent = tonumber(redis.call('HGET', KEYS[1], 'spent') or '0')
local reserved = tonumber(redis.call('HGET', KEYS[1], 'reserved') or '0')
local amount = tonumber(ARGV[2])
if not spent or not reserved or spent < 0 or reserved < 0
    or spent % 1 ~= 0 or reserved % 1 ~= 0
    or spent + reserved > 9007199254740991 then return -1 end
if redis.call('HEXISTS', KEYS[1], 'settled:' .. ARGV[1]) == 1 then return -1 end
local existing = redis.call('HGET', KEYS[1], ARGV[1])
if existing then
    if existing == ARGV[2] then return 1 else return -1 end
end
if redis.call('HGET', KEYS[1], 'blocked') == '1' then return 0 end
if spent + reserved + amount > tonumber(ARGV[3]) then return 0 end
redis.call('HSET', KEYS[1], ARGV[1], ARGV[2],
    'reserved', string.format('%.0f', reserved + amount))
redis.call('EXPIRE', KEYS[1], 604800)
return 1
"""

SETTLE_SCRIPT = """
local completed = redis.call('HGET', KEYS[1], 'settled:' .. ARGV[1])
if completed then
    if completed == ARGV[2] then return 1 else return 0 end
end
local amount = redis.call('HGET', KEYS[1], ARGV[1])
if not amount then return 0 end
local spent = tonumber(redis.call('HGET', KEYS[1], 'spent') or '0')
local reserved = tonumber(redis.call('HGET', KEYS[1], 'reserved') or '0')
local held = tonumber(amount)
local actual = tonumber(ARGV[2])
if not spent or not reserved or not held or spent < 0 or held < 0
    or reserved < held or spent % 1 ~= 0 or reserved % 1 ~= 0 or held % 1 ~= 0
    or spent + reserved - held + actual > 9007199254740991 then return 0 end
redis.call('HSET', KEYS[1], 'reserved', string.format('%.0f', reserved - held),
    'spent', string.format('%.0f', spent + actual), 'settled:' .. ARGV[1], ARGV[2])
redis.call('HDEL', KEYS[1], ARGV[1])
if actual > held then
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
        if (
            type(amount) is not int
            or type(limit) is not int
            or not 0 <= amount <= MAX_ACCOUNTING_UNITS
            or not 0 < limit <= MAX_ACCOUNTING_UNITS
        ):
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
        if accepted == -1:
            raise BudgetUnavailableError("Некорректное состояние бюджета LLM")
        if accepted != 1:
            raise BudgetExceededError("Дневной бюджет LLM исчерпан или заблокирован")
        return reservation

    async def settle(self, reservation: Reservation, actual: int) -> None:
        if type(actual) is not int or not 0 <= actual <= MAX_ACCOUNTING_UNITS:
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
        if settled != 1:
            raise BudgetUnavailableError("Резерв бюджета LLM отсутствует или изменён")
