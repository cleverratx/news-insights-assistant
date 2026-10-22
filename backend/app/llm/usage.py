from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, time, timedelta
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field
from redis.asyncio import Redis

from app.core.config import Settings
from app.llm.budget import UNITS_PER_RUBLE, Budget, RedisBudget

type BudgetMode = Literal["normal", "economy", "exhausted"]


class UsageLine(BaseModel):
    task: str
    model: str
    calls: int = Field(ge=0)
    cost_rub: Decimal = Field(ge=0)


class UsageReport(BaseModel):
    day: str = Field(description="Бюджетные сутки в часовом поясе LLM_BUDGET_TIMEZONE")
    timezone: str
    resets_at: datetime
    mode: BudgetMode = Field(
        description="normal — обычная маршрутизация; economy — сложные задачи идут в более "
        "дешёвую модель; exhausted — новые вызовы отклоняются до сброса"
    )
    limit_rub: Decimal
    spent_rub: Decimal
    reserved_rub: Decimal
    remaining_rub: Decimal
    used_share: float = Field(ge=0)
    bulk_limit_rub: Decimal = Field(
        description="Потолок массовых задач: classify, extract, summarize"
    )
    economy_from_rub: Decimal
    calls: int = Field(ge=0)
    pricing_mode: Literal["explicit", "public_estimate"]
    by_task: list[UsageLine] = Field(default_factory=list)


def rub(units: int | Decimal) -> Decimal:
    return (Decimal(units) / UNITS_PER_RUBLE).quantize(Decimal("0.000001"))


async def report(settings: Settings, budget: Budget, now: datetime | None = None) -> UsageReport:
    zone = ZoneInfo(settings.llm_budget_timezone)
    moment = (now or datetime.now(zone)).astimezone(zone)
    day = moment.date()
    usage = await budget.usage(day.isoformat())
    limit = int(settings.llm_daily_budget_rub * UNITS_PER_RUBLE)
    economy_from = limit * settings.llm_economy_share
    remaining = max(0, limit - usage.spent - usage.reserved)
    mode: BudgetMode = "normal"
    if usage.blocked or remaining == 0:
        mode = "exhausted"
    elif settings.llm_economy_share < 1 and usage.spent >= economy_from:
        mode = "economy"
    lines = []
    for label, spending in usage.by_label.items():
        task, _, model = label.partition(":")
        lines.append(
            UsageLine(task=task, model=model, calls=spending.calls, cost_rub=rub(spending.units))
        )
    return UsageReport(
        day=day.isoformat(),
        timezone=settings.llm_budget_timezone,
        resets_at=datetime.combine(day + timedelta(days=1), time(), zone),
        mode=mode,
        limit_rub=rub(limit),
        spent_rub=rub(usage.spent),
        reserved_rub=rub(usage.reserved),
        remaining_rub=rub(remaining),
        used_share=round(usage.spent / limit, 4),
        bulk_limit_rub=rub(int(limit * settings.llm_bulk_budget_share)),
        economy_from_rub=rub(economy_from),
        calls=sum(line.calls for line in lines),
        pricing_mode=settings.llm_pricing_mode,
        by_task=sorted(lines, key=lambda line: (-line.cost_rub, line.task, line.model)),
    )


@asynccontextmanager
async def open_budget(settings: Settings) -> AsyncIterator[RedisBudget]:
    async with Redis.from_url(
        settings.redis_url.get_secret_value(), socket_connect_timeout=5, socket_timeout=5
    ) as redis:
        yield RedisBudget(redis)
