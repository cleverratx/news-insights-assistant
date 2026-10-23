from typing import Any

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

from app.agent.insight import generate_with_trace
from app.api import insights as api
from app.api.companies import get_connection
from app.api.feed import build_feed, get_llm
from app.contracts.knowledge import FactCategory, FactSource, KnowledgeBase
from app.main import create_app
from app.profile.knowledge import apply_knowledge, new_fact
from app.rank.roles import Role, weights_for
from app.rank.rules import match
from app.rank.score import score
from app.rank.snapshots import INSERT, SELECT, restore, store
from tests.test_agent_trace import Store
from tests.test_data_loader import database as database
from tests.test_news_classify import NOW
from tests.test_rank_rules import item, profile


class Snapshots(Store):
    def __init__(self):
        super().__init__()
        self.snapshots = {}

    async def execute(self, sql: str, *args: Any):
        if sql == INSERT:
            self.snapshots[(args[0], args[1])] = {"item": args[3]}
        else:
            await super().execute(sql, *args)

    async def fetchrow(self, sql: str, *args: Any):
        if sql == SELECT:
            return self.snapshots.get(tuple(args))
        return await super().fetchrow(sql, *args)


async def prepare(db):
    company = profile()
    db.news = db.news.model_copy(
        update={"classification": item(segments=["oil_gas"]).classification}
    )
    feed = await build_feed(
        company, [db.news], None, now=NOW, role=weights_for(Role.CFO), min_importance=0
    )
    return company, await store(db, feed, company, [db.news])


async def test_snapshot_reuses_scores_and_rejects_stale_profile_role_and_material():
    db = Snapshots()
    company, feed = await prepare(db)
    cards = await restore(db, feed.snapshot_id, company, [db.news], "cfo")
    assert cards == feed.items
    result, trace = await generate_with_trace(
        company,
        [db.news],
        db,
        None,
        role=Role.CFO,
        ranking=cards,
        feed_snapshot_id=feed.snapshot_id,
    )
    assert result.ranking[0].impact == cards[0].impact
    assert result.ranking[0].importance == cards[0].importance
    assert result.ranking[0].score == cards[0].score
    assert result.role == trace.role == "cfo"
    assert result.ranking == trace.ranking
    assert result.usage.calls == 0
    assert "Финансовый директор" not in trace.objective
    assert result.role_view is None
    with pytest.raises(ValueError):
        await restore(db, feed.snapshot_id, company, [db.news], "logistics")
    with pytest.raises(ValueError):
        await restore(
            db,
            feed.snapshot_id,
            company.model_copy(update={"ownership": "изменено"}),
            [db.news],
            "cfo",
        )
    with pytest.raises(ValueError):
        await restore(
            db,
            feed.snapshot_id,
            company,
            [db.news.model_copy(update={"text": "новый текст"})],
            "cfo",
        )
    with pytest.raises(ValueError):
        await restore(
            db,
            feed.snapshot_id,
            company.model_copy(update={"company_id": "U_other"}),
            [db.news],
            "cfo",
        )


async def test_snapshot_round_trip_postgres(database: asyncpg.Connection):  # noqa: F811
    db = Snapshots()
    company, feed = await prepare(db)
    saved = await store(database, feed, company, [db.news])
    assert await restore(database, saved.snapshot_id, company, [db.news], "cfo") == feed.items


def test_remote_region_is_lowered_and_market_from_client_is_used():
    company = profile()
    baseline = score(company, item(segments=["oil_gas"]), NOW)
    remote = score(company, item(segments=["oil_gas"], regions=["Приморский край"]), NOW)
    assert 0 < remote.final < baseline.final
    fact = new_fact(
        company.company_id,
        FactCategory.MARKETS,
        "Продаём в Приморском крае",
        FactSource.CLIENT,
        key="markets.regions",
        value="Приморский край; Татарстан",
    )
    company = apply_knowledge(company, KnowledgeBase(company_id=company.company_id, facts=[fact]))
    found = match(company, item(regions=["Приморский край"]))
    assert any(h.feature_id == "feature:knowledge:markets.regions" for h in found.hits)
    assert not found.penalties
    assert found.score > match(profile(), item(regions=["Приморский край"])).score


async def test_api_uses_server_snapshot_and_reports_stale_context(monkeypatch):
    db = Snapshots()
    company, feed = await prepare(db)
    app = create_app()

    async def connection():
        yield db

    async def company_profile(company_id, connection):
        return company

    monkeypatch.setattr(api, "company_profile", company_profile)
    app.dependency_overrides[get_connection] = connection
    app.dependency_overrides[get_llm] = lambda: None
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        body = {"news_ids": [db.news.id], "role": "cfo", "feed_snapshot_id": feed.snapshot_id}
        result = await client.post("/api/companies/U_1/insights", json=body)
        assert result.status_code == 200
        assert result.json()["ranking"][0]["importance"] == feed.items[0].importance.model_dump(
            mode="json"
        )
        body["role"] = "logistics"
        assert (await client.post("/api/companies/U_1/insights", json=body)).status_code == 409


async def test_remote_background_can_be_displayed_below_threshold():
    from tests.test_api_feed import material

    region = material("remote", "Региональное событие", regions=["Приморский край"])
    visible = await build_feed(profile(), [region], None, now=NOW, min_importance=0)
    assert [card.news_id for card in visible.items] == ["remote"]
    assert visible.items[0].importance.score < 3.5
    assert visible.warnings
    hidden = await build_feed(profile(), [region], None, now=NOW, min_importance=3.5)
    assert hidden.items == [] and hidden.hidden_below_threshold == 1


async def test_ready_insight_refines_impact_from_its_conclusions():
    from app.agent.tools import Decision
    from tests.test_agent_critic import draft
    from tests.test_agent_tools import Replies

    db = Snapshots()
    company, feed = await prepare(db)
    cards = await restore(db, feed.snapshot_id, company, [db.news], "cfo")
    original = Decision(
        action="tool",
        reason="Найти позицию банка",
        tool="get_original",
        arguments={"doc_id": db.bank.id, "fragment_id": db.bank.fragments[1].id},
    ).model_dump_json()
    finish = Decision(action="finish", reason="Готово", output=draft(db)).model_dump_json()
    refined = (
        '{"assessments":[{"id":"n1","answers":[{"q":"q14","level":-1,'
        '"reason":"Ставка удержит долг дорогим"}],"rationale":"Долг дороже"}]}'
    )
    llm = Replies(original, finish, '{"issues":[]}', refined)
    result, trace = await generate_with_trace(
        company,
        [db.news],
        db,
        llm,
        role=Role.CFO,
        ranking=cards,
        feed_snapshot_id=feed.snapshot_id,
    )
    assert result.insight.status == "ready"
    assert result.insight.relevance == cards[0].score.final
    assert result.insight.significance == cards[0].significance
    impact = result.ranking[0].impact
    assert impact != cards[0].impact
    assert [(a.question_id, a.level) for a in impact.answers] == [("money.debt", -1)]
    assert impact.score == -1 and impact.method == "llm"
    assert "проверенные выводы разбора" in llm.messages[-1][1].content
    assert result.ranking == trace.ranking
    assert result.usage.calls == len(llm.messages) == 4
