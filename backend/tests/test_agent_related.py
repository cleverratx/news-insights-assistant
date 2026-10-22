from typing import Any

import pytest
from pydantic import ValidationError

from app.agent.critic import InsightCritic
from app.agent.insight import generate_with_trace
from app.agent.related import relation_for, with_relation
from app.agent.tools import Decision
from app.api import insights as insights_api
from app.contracts.generation import GeneratedInsight
from app.contracts.links import LinkedCompanies, LinkedCompanyCard, LinkOrigin, Turnover
from app.contracts.profile import CounterpartyRole, FeatureKind
from app.contracts.related_feed import CompanyMention
from tests.test_agent_critic import context, draft
from tests.test_agent_tools import Replies
from tests.test_agent_trace import Store
from tests.test_agent_trace import client as client
from tests.test_rank_rules import profile
from tests.test_related_feed import linked, rivals


def supplier(**changes: Any) -> LinkedCompanies:
    base = linked()
    card = LinkedCompanyCard(
        id="U_42",
        company_id="U_42",
        name="Кузбассразрезуголь",
        origins=[LinkOrigin.PAYMENTS],
        outgoing=Turnover(amount_rub=1e9, amount_share=0.12),
        **changes,
    )
    return base.model_copy(update={"cards": [*base.cards, card]})


def about(db: Store, name: str) -> Any:
    db.news = db.news.model_copy(
        update={"title": f"{name} сократила добычу", "text": f"{name} сократила добычу угля."}
    )
    return db.news


def test_competitor_relation_is_grounded_in_list_and_mention() -> None:
    db = Store()
    news = about(db, "Северсталь")
    relation = relation_for("client:rival", [news], rivals(), linked())
    assert relation is not None
    assert relation.mention.relationship == "competitor"
    assert relation.feature.id == "feature:relation:client:rival"
    assert relation.feature.kind is FeatureKind.COMPETITOR
    assert relation.feature.label.startswith("Конкурент на вашем рынке: ПАО «Северсталь»")
    assert "Назвал клиент" in relation.feature.label
    assert relation.subject == "конкурента «ПАО «Северсталь»»"
    assert (
        relation_for("client:rival", [about(Store(), "Металлоинвест")], rivals(), linked()) is None
    )
    assert relation_for("client:excluded", [news], rivals(), linked()) is None
    assert relation_for("unknown", [news], rivals(), linked()) is None


def test_counterparty_relation_carries_role_and_payment_share() -> None:
    news = about(Store(), "Кузбассразрезуголь")
    relation = relation_for("U_42", [news], rivals(), supplier())
    assert relation is not None and relation.role is CounterpartyRole.SUPPLIER
    assert relation.feature.label == "Поставщик: Кузбассразрезуголь — получает 12% ваших платежей"
    assert "затраты" in relation.exposure.reason
    assert relation.subject == "поставщика «Кузбассразрезуголь»"
    group_only = supplier()
    card = group_only.cards[-1].model_copy(update={"origins": [LinkOrigin.GROUP]})
    group_only = group_only.model_copy(update={"cards": [*group_only.cards[:-1], card]})
    assert relation_for("U_42", [news], rivals(), group_only) is None


def related_case() -> tuple[Store, Any, dict[str, Any]]:
    db = Store()
    news = about(db, "Кузбассразрезуголь")
    relation = relation_for("U_42", [news], rivals(), supplier())
    assert relation is not None
    value = draft(db)
    value["news_ids"] = [db.news.id, db.bank.id]
    value["sources"].append(
        {
            "id": "rel",
            "type": "profile_feature",
            "ref_id": relation.feature.id,
            "title": "Поставщик",
        }
    )
    value["why_it_matters"][0]["source_ids"] = ["n", "rel"]
    value["why_it_matters"][0]["text"] = "Поставщик угля сокращает добычу: под угрозой закупки"
    return db, relation, value


async def test_meaning_must_rest_on_the_relation() -> None:
    db, relation, value = related_case()
    company = with_relation(profile(), relation)
    detached = {**value, "why_it_matters": draft(db)["why_it_matters"]}
    ctx = context(db).model_copy(update={"profile": company, "news": [db.news, db.bank]})
    llm = Replies()
    report = await InsightCritic(llm).review(ctx, detached)
    assert not report.passed and not llm.messages
    assert any(relation.feature.id in issue.detail for issue in report.issues)
    passed = await InsightCritic(Replies('{"issues":[]}')).review(ctx, value)
    assert passed.passed


def finish(value: dict[str, Any]) -> str:
    return Decision(action="finish", reason="Готово", output=value).model_dump_json()


async def test_related_insight_splits_digest_and_meaning() -> None:
    db, relation, value = related_case()
    llm = Replies(finish(value), '{"issues":[]}')
    result, trace = await generate_with_trace(
        with_relation(profile(), relation), [db.news, db.bank], db, llm, relation=relation
    )
    assert result.insight.status == "ready"
    frame = result.related
    assert frame is not None and frame.company.name == "Кузбассразрезуголь"
    assert frame.digest == ["event"]
    assert frame.meaning == ["why", "risk", "action"]
    assert frame.relation.startswith("Поставщик: Кузбассразрезуголь")
    titles = {block.id: block.title for block in result.presentation.blocks}
    assert titles["event"] == "Что произошло у Кузбассразрезуголь"
    assert titles["relevance"] == "Что это значит для нас"
    assert relation.feature.id in {f.id for f in trace.profile_features}
    payload = result.model_dump(mode="json")
    payload["related"]["digest"] = ["risk"]
    with pytest.raises(ValidationError):
        GeneratedInsight.model_validate(payload)


async def test_api_builds_related_insight_or_rejects_unknown_company(
    client: Any,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    http, db = client
    about(db, "Кузбассразрезуголь")

    async def competitors(connection: Any, company_id: str) -> Any:
        return rivals()

    async def links(connection: Any, company_id: str) -> Any:
        return supplier()

    monkeypatch.setattr(insights_api, "company_competitors", competitors)
    monkeypatch.setattr(insights_api, "company_links", links)
    body = {"news_ids": [db.news.id], "related_company_id": "U_42"}
    response = await http.post("/api/companies/U_1/insights", json=body)
    assert response.status_code == 200
    frame = response.json()["related"]
    assert frame["company"]["id"] == "U_42" and frame["meaning"] == []
    assert CompanyMention.model_validate(frame["company"]).relationship == "counterparty"
    missing = await http.post(
        "/api/companies/U_1/insights", json={**body, "related_company_id": "client:rival"}
    )
    assert missing.status_code == 422
