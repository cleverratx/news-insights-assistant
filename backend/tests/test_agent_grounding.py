from typing import Any

import pytest
from pydantic import ValidationError

from app.agent.critic import Draft, InsightCritic
from app.agent.insight import ASSUMPTION_LABEL, generate, generate_with_trace
from app.agent.tools import Decision
from app.agent.trace import claims_of, load, save
from app.contracts.insight import SourceType, Support
from app.contracts.news import FactStatus, KeyFact
from app.contracts.trace import AgentTrace
from tests.test_agent_builtin_tools import Database
from tests.test_agent_critic import context, draft
from tests.test_agent_tools import Replies
from tests.test_agent_trace import Store
from tests.test_rank_rules import profile


def ready_draft(db: Database) -> dict[str, Any]:
    value = draft(db)
    value["news_ids"] = [db.news.id, db.bank.id]
    return value


def finish(value: dict[str, Any]) -> str:
    return Decision(action="finish", reason="Готово", output=value).model_dump_json()


async def test_every_statement_is_pinned_to_fragment_or_feature() -> None:
    db = Store()
    result, trace = await generate_with_trace(
        profile(), [db.news, db.bank], db, Replies(finish(ready_draft(db)), '{"issues":[]}')
    )
    insight = result.insight
    assert insight.status == "ready"
    supports = {s.id: s.support for s in insight.statements()}
    assert supports == {
        "event": Support.SOURCE,
        "why": Support.SOURCE,
        "risk": Support.SOURCE,
        "action": Support.SOURCE,
        "bank": Support.SOURCE,
    }
    [event] = insight.what_happened
    assert [(b.ref_id, b.fragment_id) for b in event.basis] == [
        (db.news.id, db.news.fragments[0].id)
    ]
    [bank] = insight.bank_view
    assert bank.basis[0].type is SourceType.BANK_ANALYTICS
    assert bank.basis[0].fragment_id == db.bank.fragments[1].id
    [why] = insight.why_it_matters
    assert {b.type for b in why.basis} == {SourceType.NEWS, SourceType.PROFILE_FEATURE}
    assert trace.claims == claims_of(insight)
    assert [c.section for c in trace.claims][:2] == ["what_happened", "why_it_matters"]
    chunks = {
        (doc.ref_id, chunk.fragment_id): chunk.text
        for doc in trace.documents
        for chunk in doc.chunks
    }
    for claim in trace.claims:
        for basis in claim.basis:
            if basis.type is not SourceType.PROFILE_FEATURE:
                assert basis.quote in " ".join(chunks[basis.ref_id, basis.fragment_id].split())
    await save(db, result, trace)
    assert await load(db, insight.id) == (result, trace)


async def test_unconfirmed_model_text_is_marked_as_assumption() -> None:
    db = Store()
    value = ready_draft(db)
    value["sources"].append(
        {"id": "doc", "type": "news", "ref_id": db.news.id, "title": db.news.title}
    )
    value["opportunities"] = [
        {
            "id": "guess",
            "text": "Компания может нарастить поставки",
            "origin": "model",
            "source_ids": ["doc"],
            "support": "source",
            "basis": [{"source_id": "n", "type": "news", "ref_id": db.news.id}],
        }
    ]
    result, trace = await generate_with_trace(
        profile(), [db.news, db.bank], db, Replies(finish(value), '{"issues":[]}')
    )
    assert result.insight.status == "ready"
    [guess] = result.insight.opportunities
    assert guess.support is Support.ASSUMPTION and guess.basis == []
    row = next(r for r in result.presentation.ai_conclusions if r.statement.id == "guess")
    assert row.label == ASSUMPTION_LABEL
    others = [r for r in result.presentation.ai_conclusions if r.statement.id != "guess"]
    assert all(r.label == "Вывод ИИ" for r in others)
    claim = next(c for c in trace.claims if c.statement_id == "guess")
    assert claim.support is Support.ASSUMPTION


async def test_consequence_that_does_not_link_event_and_company_is_an_assumption() -> None:
    db = Store()
    value = ready_draft(db)
    model = {"origin": "model"}
    value["risks"] = [
        {**model, "id": "linked", "text": "Перевозки подорожают", "source_ids": ["n", "p"]},
        {**model, "id": "industry", "text": "Отрасль заплатит больше", "source_ids": ["n"]},
        {**model, "id": "company", "text": "Компания уязвима", "source_ids": ["p"]},
        {
            **model,
            "id": "unsure",
            "text": "Возможна задержка поставок",
            "source_ids": ["n", "p"],
            "confidence": "low",
        },
    ]
    value["actions"].append(
        {
            **model,
            "id": "what_if",
            "text": "Если тарифы вырастут, пересмотреть логистику",
            "kind": "scenario",
            "source_ids": ["n", "p"],
        }
    )
    result, _ = await generate_with_trace(
        profile(), [db.news, db.bank], db, Replies(finish(value), '{"issues":[]}')
    )
    supports = {s.id: s.support for s in result.insight.statements()}
    assert supports["linked"] is Support.SOURCE
    assert supports["action"] is Support.SOURCE
    assert {supports[key] for key in ("industry", "company", "unsure", "what_if")} == {
        Support.ASSUMPTION
    }
    assert supports["why"] is Support.SOURCE
    labels = {r.statement.id: r.label for r in result.presentation.ai_conclusions}
    assert labels["industry"] == ASSUMPTION_LABEL and labels["linked"] == "Вывод ИИ"


async def test_fact_without_fragment_is_rejected_by_critic() -> None:
    db = Database()
    value = draft(db)
    value["sources"][0].pop("quote")
    llm = Replies()
    report = await InsightCritic(llm).review(context(db), value)
    assert not report.passed and not llm.messages
    assert any(
        issue.statement_id == "event" and "fragment_id" in issue.detail for issue in report.issues
    )


def test_model_cannot_supply_server_grounding_fields() -> None:
    db = Database()
    value = draft(db)
    value["risks"][0]["support"] = "source"
    value["risks"][0]["basis"] = [{"source_id": "x", "type": "news", "ref_id": "fake"}]
    parsed = Draft.model_validate(value)
    assert parsed.risks[0].support is None and parsed.risks[0].basis == []
    schema = InsightCritic.output_schema()
    assert "basis" not in str(schema) and "support" not in str(schema)


async def test_fallback_facts_keep_fragment_links() -> None:
    db = Database()
    fragment = db.news.fragments[0]
    db.news = db.news.model_copy(
        update={
            "key_facts": [
                KeyFact(
                    id="kf1",
                    text="Экспорт угля вырос",
                    status=FactStatus.CONFIRMED,
                    fragment_id=fragment.id,
                )
            ]
        }
    )
    result = await generate(profile(), [db.news], db, None)
    [fact] = result.insight.what_happened
    assert fact.support is Support.SOURCE
    assert fact.basis[0].fragment_id == fragment.id
    assert fact.basis[0].quote and fact.basis[0].quote in " ".join(fragment.text.split())


async def test_trace_rejects_claims_not_backed_by_snapshot() -> None:
    db = Store()
    _, trace = await generate_with_trace(
        profile(), [db.news, db.bank], db, Replies(finish(ready_draft(db)), '{"issues":[]}')
    )
    payload = trace.model_dump(mode="json")
    forged = payload["claims"][0]
    forged["basis"][0]["quote"] = "Выдуманная цитата"
    with pytest.raises(ValidationError):
        AgentTrace.model_validate(payload)
    payload = trace.model_dump(mode="json")
    payload["claims"][0]["basis"] = []
    with pytest.raises(ValidationError):
        AgentTrace.model_validate(payload)
