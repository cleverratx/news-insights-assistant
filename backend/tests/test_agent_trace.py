import copy
import json
from typing import Any

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app.agent.insight import generate_with_trace
from app.agent.orchestrator import Limits
from app.agent.tools import Decision
from app.agent.trace import INSERT, SCHEMA, SELECT, ensure_storage, load, save
from app.api import insights as api
from app.api.companies import get_connection
from app.api.feed import get_llm
from app.contracts.trace import AgentTrace
from app.main import create_app
from tests.test_agent_builtin_tools import Database
from tests.test_agent_insight import finish
from tests.test_agent_tools import Replies
from tests.test_data_loader import database as database
from tests.test_rank_rules import profile


class Store(Database):
    def __init__(self) -> None:
        super().__init__()
        self.saved: dict[str, dict[str, str]] = {}
        self.write_error = False
        self.read_error = False

    async def execute(self, sql: str, *args: Any) -> None:
        if sql == INSERT:
            if self.write_error:
                raise asyncpg.PostgresError("private-db-detail")
            if args[0] in self.saved:
                raise asyncpg.UniqueViolationError("duplicate")
            self.saved[args[0]] = {"result": args[4], "trace": args[5]}
        await super().execute(sql, *args)

    async def fetchrow(self, sql: str, *args: Any) -> dict | None:
        if sql == SELECT:
            if self.read_error:
                raise asyncpg.PostgresError("private-db-detail")
            return copy.deepcopy(self.saved.get(args[0]))
        return await super().fetchrow(sql, *args)


async def test_ready_trace_records_plan_sources_and_checks_without_drafts() -> None:
    db = Store()
    company = profile()
    result, trace = await generate_with_trace(
        company, [db.news, db.bank], db, Replies(finish(db), '{"issues":[]}')
    )
    assert trace.insight_id == result.insight.id
    assert trace.run_id == result.insight.run_id
    assert trace.company_id == company.company_id
    assert trace.steps[-1].action == "finish"
    assert trace.steps[-1].goal == "Готово"
    assert trace.checks[0].passed
    assert trace.used_sources == result.insight.sources
    assert {s.ref_id for s in trace.used_sources} <= (
        {d.ref_id for d in trace.documents} | {f.id for f in trace.profile_features}
    )
    assert trace.usage == result.usage
    assert "draft" not in trace.model_dump_json()
    assert "messages" not in trace.model_dump_json()
    assert AgentTrace.model_validate_json(trace.model_dump_json()) == trace
    company.features.clear()
    assert trace.profile_features
    assert trace.finished_at >= trace.started_at


async def test_tool_reuse_and_original_snapshot_survive_storage() -> None:
    db = Store()
    tool = Decision(
        action="tool",
        reason="Проверить оригинал",
        tool="get_original",
        arguments={"doc_id": db.bank.id, "fragment_id": db.bank.fragments[1].id},
    ).model_dump_json()
    result, trace = await generate_with_trace(
        profile(),
        [db.news, db.bank],
        db,
        Replies(tool, tool, finish(db), '{"issues":[]}'),
    )
    assert result.insight.status == "ready"
    assert trace.steps[0].tool == "get_original"
    assert trace.steps[1].reused_from == trace.steps[0].observation_id
    assert len(trace.observations) == 1
    assert trace.observations[0].observation.data["text"] == db.bank.fragments[1].text
    await ensure_storage(db)
    await save(db, result, trace)
    db.bank = db.bank.model_copy(update={"text": "Материал изменён после разбора"})
    assert await load(db, result.insight.id) == (result, trace)
    assert len([call for call in db.calls if call[0] == INSERT]) == 1


@pytest.mark.parametrize("mode", ["unavailable", "planner_error", "step_limit"])
async def test_failed_run_is_also_traceable(mode: str) -> None:
    db = Store()
    llm = None
    limits = None
    if mode == "planner_error":
        llm = Replies(RuntimeError("private-provider-detail"))
    if mode == "step_limit":
        llm = Replies(
            Decision(
                action="tool",
                reason="Проверить источник",
                tool="get_original",
                arguments={"doc_id": db.news.id},
            ).model_dump_json()
        )
        limits = Limits(max_steps=1)
    result, trace = await generate_with_trace(profile(), [db.news], db, llm, limits=limits)
    assert result.insight.status == "insufficient_data"
    assert trace.execution_status == mode
    assert "private-provider-detail" not in trace.model_dump_json()
    await save(db, result, trace)
    assert await load(db, result.insight.id) == (result, trace)


async def test_storage_rejects_mismatched_trace_and_preserves_first_write() -> None:
    db = Store()
    result, trace = await generate_with_trace(profile(), [db.news], db, None)
    with pytest.raises(ValueError):
        await save(db, result, trace.model_copy(update={"run_id": "another-run"}))
    assert not db.saved
    await save(db, result, trace)
    with pytest.raises(asyncpg.UniqueViolationError):
        await save(db, result, trace)
    assert await load(db, result.insight.id) == (result, trace)
    assert await load(db, "missing") is None


async def test_trace_validation_rejects_dangling_sources() -> None:
    db = Store()
    _, trace = await generate_with_trace(profile(), [db.news], db, None)
    payload = trace.model_dump(mode="json")
    payload["documents"] = []
    with pytest.raises(ValidationError):
        AgentTrace.model_validate(payload)


@pytest.fixture
async def client(monkeypatch):
    db = Store()
    app = create_app()

    async def connection():
        yield db

    async def company(company_id, connection):
        return profile()

    monkeypatch.setattr(api, "company_profile", company)
    app.dependency_overrides[get_connection] = connection
    app.dependency_overrides[get_llm] = lambda: None
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http, db


async def test_api_create_then_read_persisted_insight_and_trace(client) -> None:
    http, db = client
    created = await http.post("/api/companies/U_1/insights", json={"news_ids": [db.news.id]})
    assert created.status_code == 200
    payload = created.json()
    insight_id = payload["insight"]["id"]
    fetched = await http.get(f"/api/insights/{insight_id}")
    assert fetched.status_code == 200 and fetched.json() == payload
    traced = await http.get(f"/api/insights/{insight_id}/trace")
    assert traced.status_code == 200
    trace = traced.json()
    assert trace["run_id"] == payload["insight"]["run_id"]
    assert trace["execution_status"] == "unavailable"
    assert trace["documents"][0]["ref_id"] == db.news.id
    assert len(db.saved) == 1
    # GET не генерирует новый разбор и не требует повторного обращения к LLM.
    assert len([sql for sql, _ in db.calls if sql == SCHEMA]) == 1


@pytest.mark.parametrize("suffix", ["", "/trace"])
async def test_api_missing_analysis_is_404(client, suffix: str) -> None:
    http, _ = client
    response = await http.get(f"/api/insights/missing{suffix}")
    assert response.status_code == 404


async def test_api_failed_insert_does_not_claim_success(client) -> None:
    http, db = client
    db.write_error = True
    response = await http.post("/api/companies/U_1/insights", json={"news_ids": [db.news.id]})
    assert response.status_code == 503
    assert "private-db-detail" not in response.text
    assert not db.saved


@pytest.mark.parametrize("kind", ["invalid_json", "wrong_run", "database_error"])
async def test_api_corrupt_or_unavailable_storage_is_503(client, kind: str) -> None:
    http, db = client
    created = await http.post("/api/companies/U_1/insights", json={"news_ids": [db.news.id]})
    insight_id = created.json()["insight"]["id"]
    if kind == "database_error":
        db.read_error = True
    elif kind == "invalid_json":
        db.saved[insight_id]["trace"] = "{}"
    else:
        trace = json.loads(db.saved[insight_id]["trace"])
        trace["run_id"] = "different-run"
        db.saved[insight_id]["trace"] = json.dumps(trace)
    response = await http.get(f"/api/insights/{insight_id}/trace")
    assert response.status_code == 503
    assert "private-db-detail" not in response.text


async def test_postgres_round_trip_and_immutable_insert(
    database: asyncpg.Connection,  # noqa: F811
) -> None:
    db = Store()
    result, trace = await generate_with_trace(profile(), [db.news], db, None)
    await ensure_storage(database)
    await ensure_storage(database)
    await save(database, result, trace)
    assert await load(database, result.insight.id) == (result, trace)
    with pytest.raises(asyncpg.UniqueViolationError):
        await save(database, result, trace)
    assert await load(database, result.insight.id) == (result, trace)


async def test_failed_tool_and_corrected_finish_remain_in_trace() -> None:
    class FailingStore(Store):
        async def fetchrow(self, sql: str, *args: Any) -> dict | None:
            if args and args[0] == "broken":
                raise asyncpg.PostgresError("private-tool-detail")
            return await super().fetchrow(sql, *args)

    db = FailingStore()
    bad = json.loads(finish(db))
    bad["output"]["sources"][0]["quote"] = "Выдуманный черновик"
    missing = Decision(
        action="tool",
        reason="Проверить недоступный инструмент",
        tool="get_original",
        arguments={"doc_id": "broken"},
    ).model_dump_json()
    result, trace = await generate_with_trace(
        profile(),
        [db.news, db.bank],
        db,
        Replies(missing, json.dumps(bad), finish(db), '{"issues":[]}'),
    )
    assert result.insight.status == "ready"
    assert trace.steps[0].status == "error"
    assert trace.steps[0].error_detail.code == "tool_error"
    assert [s.check_index for s in trace.steps[1:]] == [0, 1]
    assert [report.passed for report in trace.checks] == [False, True]
    assert [s.status for s in trace.steps[1:]] == ["error", "ok"]
    assert "Выдуманный черновик" not in trace.model_dump_json()


@pytest.mark.parametrize(
    "field,value",
    [
        ("quote", "Цитата, которой не было"),
        ("fragment_id", "несуществующий-фрагмент"),
        ("type", "bank_analytics"),
    ],
)
async def test_trace_rejects_source_not_supported_by_snapshot(field: str, value: str) -> None:
    db = Store()
    _, trace = await generate_with_trace(profile(), [db.news], db, None)
    payload = trace.model_dump(mode="json")
    payload["used_sources"][0][field] = value
    with pytest.raises(ValidationError):
        AgentTrace.model_validate(payload)


async def test_timed_out_critic_is_not_reported_as_success() -> None:
    import asyncio

    class SlowCritic(Replies):
        async def complete(self, task, messages):
            if self.messages:
                await asyncio.sleep(1)
            return await super().complete(task, messages)

    db = Store()
    result, trace = await generate_with_trace(
        profile(),
        [db.news, db.bank],
        db,
        SlowCritic(finish(db), '{"issues":[]}'),
        limits=Limits(timeout_seconds=0.05),
    )
    assert result.execution_status == "timeout"
    assert trace.steps[-1].action == "finish"
    assert trace.steps[-1].status == "timeout"
    assert trace.steps[-1].check_index is None


async def test_api_generation_runs_tools_critic_and_persists_their_evidence(client) -> None:
    http, db = client
    actions = [
        ("get_profile", {}),
        ("search_bank_analytics", {"query": "ставка"}),
        ("get_original", {"doc_id": db.bank.id, "fragment_id": db.bank.fragments[1].id}),
        ("calculate_exposure", {"direction": "incoming", "partner_ids": ["U_2"]}),
    ]
    replies = Replies(
        *[
            Decision(
                action="tool", reason="Уточнить основания", tool=name, arguments=args
            ).model_dump_json()
            for name, args in actions
        ],
        finish(db),
        '{"issues":[]}',
    )
    http._transport.app.dependency_overrides[get_llm] = lambda: replies
    response = await http.post(
        "/api/companies/U_1/insights",
        json={"news_ids": [db.news.id, db.bank.id]},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["insight"]["status"] == "ready"
    trace = (await http.get(f"/api/insights/{payload['insight']['id']}/trace")).json()
    assert [s["tool"] for s in trace["steps"][:-1]] == [name for name, _ in actions]
    assert all(s["status"] == "ok" for s in trace["steps"])
    assert len(trace["observations"]) == 4
    assert trace["checks"][-1]["passed"]
    assert trace["used_sources"] == payload["insight"]["sources"]
    assert len(replies.messages) == 6
