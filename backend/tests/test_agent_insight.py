import json
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.agent.insight import InsightPlanner, generate
from app.agent.orchestrator import Limits
from app.agent.tools import Decision
from app.api.insights import InsightRequest, generate_insight
from app.contracts.generation import GeneratedInsight
from tests.test_agent_builtin_tools import Database, context
from tests.test_agent_critic import draft
from tests.test_agent_tools import Replies
from tests.test_rank_rules import profile


def finish(db: Database) -> str:
    value = draft(db)
    value["news_ids"] = [db.news.id, db.bank.id]
    return Decision(action="finish", reason="Готово", output=value).model_dump_json()


async def test_generation_verified_sections_origins_and_usage() -> None:
    db = Database()
    llm = Replies(finish(db), '{"issues":[]}')
    result = await generate(profile(), [db.news, db.bank], db, llm, focus="Логистика")
    assert result.insight.status == "ready"
    assert result.insight.self_check.passed
    assert result.insight.self_check.iterations == 1
    assert len(result.presentation.blocks) == 6
    assert result.presentation.facts[0].statement.origin == "fact"
    assert result.presentation.bank_position[0].statement.origin == "bank"
    assert all(s.statement.origin == "model" for s in result.presentation.ai_conclusions)
    assert result.presentation.originals[0].quote == db.news.fragments[0].text
    assert result.usage.calls == 2
    assert result.usage.known_cost_rub == Decimal("0.10")
    assert "Логистика" in llm.messages[0][1].content
    assert GeneratedInsight.model_validate_json(result.model_dump_json()) == result
    payload = result.model_dump(mode="json")
    payload["presentation"]["facts"][0]["statement"]["text"] = "Подменённый факт"
    with pytest.raises(ValidationError):
        GeneratedInsight.model_validate(payload)


async def test_unavailable_returns_sources_without_unverified_claims() -> None:
    db = Database()
    result = await generate(profile(), [db.news], db, None)
    assert result.insight.status == "insufficient_data"
    assert result.execution_status == "unavailable"
    assert not result.insight.what_happened and not result.insight.actions
    assert result.presentation.originals[0].ref_id == db.news.id
    assert result.usage.calls == 0
    assert not db.calls


async def test_rejected_draft_is_hidden_and_paid_calls_are_counted() -> None:
    db = Database()
    value = draft(db)
    value["sources"][0]["quote"] = "Выдуманная цитата"
    reply = Decision(action="finish", reason="Готово", output=value).model_dump_json()
    result = await generate(
        profile(), [db.news], db, Replies(reply, reply), limits=Limits(max_critic_reviews=2)
    )
    assert result.insight.status == "insufficient_data"
    assert not result.insight.self_check.passed
    assert not result.presentation.facts and not result.presentation.ai_conclusions
    assert "Выдуманная цитата" not in result.model_dump_json()
    assert result.usage.calls == 2


async def test_planner_failure_returns_nonready() -> None:
    db = Database()
    result = await generate(profile(), [db.news], db, Replies(RuntimeError("offline")))
    assert result.execution_status == "planner_error"
    assert result.insight.status == "insufficient_data"


def test_model_excerpt_bounded_without_changing_server_evidence() -> None:
    db = Database()
    ctx = context(db)
    ctx.news[0] = db.news.model_copy(update={"text": "а" * 12000})
    payload = InsightPlanner(Replies()).context_data(ctx)
    assert len(payload["news"][0]["text_excerpt"]) == 2400
    assert payload["news"][0]["truncated"]
    assert len(ctx.news[0].text) == 12000
    json.dumps(payload)


@pytest.mark.parametrize("ids", [[], ["n", "n"], ["1", "2", "3", "4"]])
def test_request_rejects_invalid_material_selection(ids: list[str]) -> None:
    with pytest.raises(ValidationError):
        InsightRequest(news_ids=ids)


async def test_endpoint_loads_selected_material_and_returns_generation(monkeypatch) -> None:
    import app.api.insights as api

    async def load(company_id, connection):
        assert company_id == "U_1"
        return profile()

    monkeypatch.setattr(api, "company_profile", load)
    db = Database()
    result = await generate_insight("U_1", InsightRequest(news_ids=[db.news.id]), db, None)
    assert result.insight.news_ids == [db.news.id]
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await generate_insight("U_1", InsightRequest(news_ids=["missing"]), db, None)
    assert exc.value.status_code == 404


@pytest.mark.parametrize("case", ["missing_event", "bank_in_model_block"])
async def test_generation_critic_rejects_incomplete_or_mixed_origin(case: str) -> None:
    from app.agent.insight import GenerationCritic
    from tests.test_agent_critic import context as critic_context

    db = Database()
    value = draft(db)
    if case == "bank_in_model_block":
        value["news_ids"] = [db.news.id, db.bank.id]
        value["risks"][0]["origin"] = "bank"
    llm = Replies()
    report = await GenerationCritic(llm).review(critic_context(db), value)
    assert not report.passed and not report.semantic_checked
    assert not llm.messages


def test_insight_route_is_registered_in_openapi() -> None:
    from app.main import app

    operation = app.openapi()["paths"]["/api/companies/{company_id}/insights"]["post"]
    assert operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/GeneratedInsight"
    )


@pytest.mark.parametrize("case", ["wrong_section", "unknown_statement", "unknown_source"])
async def test_presentation_rejects_invalid_block_references(case: str) -> None:
    db = Database()
    result = await generate(profile(), [db.news, db.bank], db, Replies(finish(db), '{"issues":[]}'))
    payload = result.model_dump(mode="json")
    if case == "wrong_section":
        payload["presentation"]["blocks"][0]["statement_ids"] = ["why"]
    elif case == "unknown_statement":
        payload["presentation"]["blocks"][0]["statement_ids"] = ["missing"]
    else:
        payload["presentation"]["blocks"][-1]["source_ids"] = ["missing"]
    with pytest.raises(ValidationError):
        GeneratedInsight.model_validate(payload)
