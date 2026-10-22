import copy
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.agent.insight_cache import cache_input, cached
from app.api import insights as insights_api
from app.api import llm as llm_api
from app.api.companies import get_connection
from app.api.feed import get_llm
from app.cache.store import GET_SQL, PUT_SQL
from app.core.config import Settings
from app.llm.budget import DayUsage, RedisBudget, Spending
from app.llm.client import LLMClient
from app.llm.errors import BudgetUnavailableError
from app.llm.models import AllowedModel, Message, Task
from app.llm.spend import LLMSpendMiddleware, record, spend_scope
from app.llm.usage import report
from app.main import create_app
from tests.test_agent_insight import finish
from tests.test_agent_tools import Replies
from tests.test_agent_trace import Store
from tests.test_llm import RecordingBudget, completion
from tests.test_llm import settings as settings
from tests.test_rank_rules import profile


@pytest.fixture
def config(settings: Settings) -> Settings:  # noqa: F811
    return settings


async def call(config: Settings, budget: RecordingBudget, task: Task) -> list[str]:
    models: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        models.append(request.content.decode())
        return httpx.Response(200, json=completion())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await LLMClient(config, http, budget).complete(
            task, [Message(role="user", content="текст")]
        )
    return models


async def test_task_route_overrides_tier_and_labels_spending(config: Settings) -> None:
    config.llm_routes = {Task.SUMMARIZE: AllowedModel.GLM}
    budget = RecordingBudget()
    bodies = await call(config, budget, Task.SUMMARIZE)
    assert '"z-ai/glm-5.3-flash"' in bodies[0]
    assert budget.reservations[0].label == "summarize:glm-5.3-flash"


async def test_bulk_tasks_leave_budget_for_insights(config: Settings) -> None:
    budget = RecordingBudget()
    await call(config, budget, Task.CLASSIFY)
    await call(config, budget, Task.INTERPRET)
    assert budget.limits == [300_000_000, 500_000_000]
    assert [r.label for r in budget.reservations] == [
        "classify:gemma-4-26b-a4b",
        "interpret:glm-5.3-flash",
    ]


@pytest.mark.parametrize(
    ("spent", "expected"),
    [(399_999_999, "z-ai/glm-5.3-flash"), (400_000_000, "google/gemma-4-26b-a4b-it")],
)
async def test_economy_moves_smart_tasks_to_cheaper_model(
    config: Settings, spent: int, expected: str
) -> None:
    budget = RecordingBudget(spent=spent)
    bodies = await call(config, budget, Task.INTERPRET)
    assert f'"{expected}"' in bodies[0]
    assert budget.spent_reads == 1


async def test_economy_never_picks_more_expensive_model(config: Settings) -> None:
    config.llm_model_fast, config.llm_model_smart = AllowedModel.GLM, AllowedModel.GEMMA
    bodies = await call(config, RecordingBudget(spent=499_000_000), Task.RERANK)
    assert '"google/gemma-4-26b-a4b-it"' in bodies[0]


async def test_bulk_tasks_and_disabled_economy_skip_spending_read(config: Settings) -> None:
    budget = RecordingBudget(spent=499_000_000)
    await call(config, budget, Task.EXTRACT)
    config.llm_economy_share = Decimal(1)
    bodies = await call(config, budget, Task.INTERPRET)
    assert '"z-ai/glm-5.3-flash"' in bodies[0]
    assert budget.spent_reads == 0


async def test_request_scope_counts_every_charged_attempt(config: Settings) -> None:
    statuses = iter([503, 200])

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(next(statuses), json=completion())

    budget = RecordingBudget()
    with spend_scope() as spend:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            await LLMClient(config, http, budget).complete(
                Task.CLASSIFY, [Message(role="user", content="текст")]
            )
    assert spend.calls == 2
    assert spend.units == sum(budget.charges)
    assert spend.models == ["gemma-4-26b-a4b"]
    record("glm-5.3-flash", 10)
    assert spend.calls == 2


async def test_middleware_reports_request_cost_in_headers() -> None:
    app = FastAPI()
    app.add_middleware(LLMSpendMiddleware)

    @app.get("/paid")
    async def paid() -> dict[str, str]:
        record("glm-5.3-flash", 1_500)
        record("glm-5.3-flash", 500)
        return {}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        response = await http.get("/paid")
    assert response.headers["X-LLM-Calls"] == "2"
    assert response.headers["X-LLM-Cost-Rub"] == "0.002"
    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as http:
        health = await http.get("/api/health")
    assert health.headers["X-LLM-Calls"] == "0"


class Executor:
    def __init__(self, data: object) -> None:
        self.data = data
        self.commands: list[tuple[str | int, ...]] = []

    async def execute_command(self, *args: str | int) -> object:
        self.commands.append(args)
        return self.data


async def test_redis_usage_reads_counters_by_task_and_model() -> None:
    executor = Executor(
        {
            b"spent": b"2500",
            b"reserved": b"700",
            b"request:abc": b"700",
            b"settled:request:old": b"2500",
            b"cost:interpret:glm-5.3-flash": b"2000",
            b"calls:interpret:glm-5.3-flash": b"3",
            b"cost:classify:deepseek-v4.1-flash": b"500",
            b"calls:classify:deepseek-v4.1-flash": b"5",
        }
    )
    usage = await RedisBudget(executor, "ns").usage("2026-10-22")
    assert executor.commands == [("HGETALL", "ns:2026-10-22")]
    assert (usage.spent, usage.reserved, usage.blocked) == (2500, 700, False)
    assert usage.by_label == {
        "classify:deepseek-v4.1-flash": Spending(5, 500),
        "interpret:glm-5.3-flash": Spending(3, 2000),
    }
    assert await RedisBudget(Executor(b"42")).spent("2026-10-22") == 42
    assert (await RedisBudget(Executor({})).usage("2026-10-22")).spent == 0
    with pytest.raises(BudgetUnavailableError):
        await RedisBudget(Executor({b"spent": b"-1"})).usage("2026-10-22")
    with pytest.raises(ValueError):
        await RedisBudget(Executor(1)).reserve("2026-10-22", 1, 10, "Bad Label")


class UsageBudget(RecordingBudget):
    def __init__(self, usage: DayUsage) -> None:
        super().__init__()
        self.day_usage = usage

    async def usage(self, day: str) -> DayUsage:
        assert day == self.day_usage.day
        return self.day_usage


@pytest.mark.parametrize(
    ("spent", "reserved", "blocked", "mode"),
    [
        (100_000_000, 0, False, "normal"),
        (400_000_000, 0, False, "economy"),
        (450_000_000, 50_000_000, False, "exhausted"),
        (1, 0, True, "exhausted"),
    ],
)
async def test_day_report_shows_mode_remaining_and_breakdown(
    spent: int, reserved: int, blocked: bool, mode: str
) -> None:
    labels = {
        "classify:deepseek-v4.1-flash": Spending(40, 1_200_000),
        "interpret:glm-5.3-flash": Spending(2, 3_400_000),
    }
    budget = UsageBudget(DayUsage("2026-10-22", spent, reserved, blocked, labels))
    moment = datetime(2026, 10, 22, 23, 30, tzinfo=UTC)
    result = await report(Settings(_env_file=None), budget, moment)
    assert result.mode == mode
    assert result.limit_rub == Decimal(500)
    assert result.remaining_rub == Decimal(max(0, 500_000_000 - spent - reserved)) / 1_000_000
    assert result.bulk_limit_rub == Decimal(300)
    assert result.economy_from_rub == Decimal(400)
    assert result.resets_at == datetime(2026, 10, 23, tzinfo=UTC)
    assert result.calls == 42
    assert [(line.task, line.model, line.cost_rub) for line in result.by_task] == [
        ("interpret", "glm-5.3-flash", Decimal("3.4")),
        ("classify", "deepseek-v4.1-flash", Decimal("1.2")),
    ]


async def test_usage_endpoint_reports_or_fails_safely(monkeypatch: pytest.MonkeyPatch) -> None:
    budget = UsageBudget(DayUsage(datetime.now(UTC).date().isoformat(), 5_000_000, 0, False))

    @asynccontextmanager
    async def opened(_: Settings) -> AsyncIterator[UsageBudget]:
        yield budget

    monkeypatch.setattr(llm_api, "open_budget", opened)
    app = create_app()
    app.dependency_overrides[llm_api.get_settings] = lambda: Settings(_env_file=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        response = await http.get("/api/llm/usage")
        assert response.status_code == 200
        assert response.json()["spent_rub"] == "5.000000"
        assert response.json()["mode"] == "normal"

        @asynccontextmanager
        async def broken(_: Settings) -> AsyncIterator[UsageBudget]:
            raise BudgetUnavailableError("private-redis-detail")
            yield budget

        monkeypatch.setattr(llm_api, "open_budget", broken)
        failed = await http.get("/api/llm/usage")
    assert failed.status_code == 503
    assert "private-redis-detail" not in failed.text


def test_cache_key_follows_profile_materials_and_focus() -> None:
    db = Store()
    company = profile()
    base = cache_input(company, [db.news], "Влияние  на ставку")
    assert base == cache_input(company, [db.news], " влияние на   ставку ")
    assert base != cache_input(company, [db.news], None)
    changed = db.news.model_copy(update={"text": "Материал изменён"})
    assert base != cache_input(company, [changed], "Влияние на ставку")
    edited = company.model_copy(update={"features": company.features[1:]})
    assert base != cache_input(edited, [db.news], "Влияние на ставку")


class CacheStore(Store):
    def __init__(self) -> None:
        super().__init__()
        self.cache: dict[tuple[str, str, str], dict[str, Any]] = {}

    async def execute(self, sql: str, *args: Any) -> None:
        if sql == PUT_SQL:
            kind, version, key, result, model, _, created = args
            self.cache[kind, version, key] = {
                "result": result,
                "model": model,
                "created_at": created,
            }
        await super().execute(sql, *args)

    async def fetchrow(self, sql: str, *args: Any) -> dict | None:
        if sql == GET_SQL:
            self.calls.append((sql, args))
            return copy.deepcopy(self.cache.get((args[0], args[1], args[2])))
        return await super().fetchrow(sql, *args)


@pytest.fixture
async def cache_client(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[tuple[Any, CacheStore]]:
    db = CacheStore()
    app = create_app()

    async def connection() -> AsyncIterator[CacheStore]:
        yield db

    async def company(company_id: str, connection: Any) -> Any:
        return profile()

    monkeypatch.setattr(insights_api, "company_profile", company)
    monkeypatch.setattr(insights_api, "get_settings", lambda: Settings(_env_file=None))
    app.dependency_overrides[get_connection] = connection
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http, db


async def test_ready_insight_is_reused_without_llm(cache_client: Any) -> None:
    http, db = cache_client
    app = http._transport.app
    body = {"news_ids": [db.news.id, db.bank.id], "focus": "Ставка"}
    app.dependency_overrides[get_llm] = lambda: Replies(finish(db), '{"issues":[]}')
    first = await http.post("/api/companies/U_1/insights", json=body)
    assert first.status_code == 200 and first.json()["insight"]["status"] == "ready"
    assert first.json()["cache_hit"] is False
    idle = Replies()
    app.dependency_overrides[get_llm] = lambda: idle
    again = await http.post("/api/companies/U_1/insights", json={**body, "focus": " ставка "})
    assert again.status_code == 200
    assert again.json()["cache_hit"] is True
    assert again.json()["insight"] == first.json()["insight"]
    assert again.headers["X-LLM-Calls"] == "0"
    assert not idle.messages
    assert len(db.saved) == 1
    fresh = await http.post("/api/companies/U_1/insights", json={**body, "refresh": True})
    assert fresh.json()["cache_hit"] is False
    assert len(db.saved) == 2


async def test_failed_or_expired_insight_is_not_served_from_cache(cache_client: Any) -> None:
    http, db = cache_client
    app = http._transport.app
    app.dependency_overrides[get_llm] = lambda: None
    body = {"news_ids": [db.news.id]}
    first = await http.post("/api/companies/U_1/insights", json=body)
    assert first.json()["insight"]["status"] == "insufficient_data"
    assert not db.cache
    second = await http.post("/api/companies/U_1/insights", json=body)
    assert second.json()["insight"]["id"] != first.json()["insight"]["id"]

    app.dependency_overrides[get_llm] = lambda: Replies(finish(db), '{"issues":[]}')
    ready = await http.post(
        "/api/companies/U_1/insights", json={"news_ids": [db.news.id, db.bank.id]}
    )
    assert ready.json()["insight"]["status"] == "ready"
    key = cache_input(profile(), [db.news, db.bank], None)
    later = datetime.now(UTC) + timedelta(hours=25)
    assert await cached(db, key, timedelta(hours=24), later) is None
    assert await cached(db, key, timedelta(hours=24)) is not None
