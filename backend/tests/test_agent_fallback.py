from typing import Any

import pytest
from pydantic import ValidationError

from app.agent.critic import InsightCritic
from app.agent.fallback import (
    NUMBER_ISSUE,
    data_gaps,
    figures,
    pool,
    related_questions,
    ungrounded,
)
from app.agent.insight import generate, generate_with_trace
from app.agent.orchestrator import Limits
from app.agent.tools import Decision
from app.contracts.generation import GapKind, GeneratedInsight
from app.contracts.news import FactStatus, KeyFact, Topic
from app.contracts.profile import ForeignTrade
from tests.test_agent_builtin_tools import Database
from tests.test_agent_critic import context, draft
from tests.test_agent_tools import Replies
from tests.test_rank_rules import feature, item, profile

SOURCE = "Тарифы на перевозку выросли на 12,5%, выручка отрасли — 3,3 млрд руб. за квартал."


@pytest.mark.parametrize(
    ("claim", "missing"),
    [
        ("Тарифы выросли на 12,5%", []),
        ("Рост тарифов около 13%", []),
        ("Выручка отрасли 3 300 млн руб.", []),
        ("Выручка компании 20 млрд руб.", []),
        ("В течение 3 месяцев эффект сохранится", []),
        ("Расходы вырастут на 7%", ["7"]),
        ("EBITDA снизится на 15,2%", ["15,2"]),
        ("Потери составят 450 млн руб.", ["450"]),
    ],
)
def test_numbers_must_come_from_sources_or_company_data(claim: str, missing: list[str]) -> None:
    known = pool([SOURCE, profile().model_dump_json()])
    assert ungrounded(claim, known) == missing


def test_small_counts_without_units_are_not_figures() -> None:
    assert figures("Два сценария на 2 квартала и 3 шага") == []
    assert [token for token, *_ in figures("ставка 2% и 2026 год")] == ["2", "2026"]


def with_figures(db: Database) -> Database:
    db.news = db.news.model_copy(update={"text": f"{db.news.text} {SOURCE}"})
    return db


async def test_critic_rejects_invented_figure_before_llm() -> None:
    db = with_figures(Database())
    value = draft(db)
    value["risks"][0]["text"] = "Стоимость перевозок вырастет на 37%"
    llm = Replies()
    report = await InsightCritic(llm).review(context(db), value)
    assert not report.passed and not llm.messages
    assert [(i.statement_id, i.detail.startswith(NUMBER_ISSUE)) for i in report.issues] == [
        ("risk", True)
    ]
    assert "«37%»" in report.issues[0].detail or "«37»" in report.issues[0].detail
    value["risks"][0]["text"] = "Тарифы на перевозку уже выросли на 12,5%"
    value["why_it_matters"][0]["text"] = "Компания с ОКВЭД 06.10 зависит от экспорта сырья"
    passed = await InsightCritic(Replies('{"issues":[]}')).review(context(db), value)
    assert passed.passed


def finish(value: dict[str, Any]) -> str:
    return Decision(action="finish", reason="Готово", output=value).model_dump_json()


def with_bad_risk(db: Database) -> dict[str, Any]:
    value = draft(db)
    value["news_ids"] = [db.news.id, db.bank.id]
    value["risks"].append(
        {
            "id": "invented",
            "text": "Маржа компании снизится на 4,5 п.п.",
            "origin": "model",
            "source_ids": ["n", "p"],
        }
    )
    return value


async def test_figures_without_source_are_hidden_and_rest_is_verified() -> None:
    db = with_figures(Database())
    value = with_bad_risk(db)
    llm = Replies(finish(value), finish(value), '{"issues":[]}')
    result, trace = await generate_with_trace(
        profile(), [db.news, db.bank], db, llm, limits=Limits(max_critic_reviews=2)
    )
    assert result.insight.status == "ready"
    assert result.execution_status == "completed"
    assert "invented" not in {s.id for s in result.insight.statements()}
    assert "4,5 п.п." not in result.model_dump_json()
    numbers = [g for g in result.gaps if g.kind is GapKind.NUMBERS and "Скрыто" in g.consequence]
    assert len(numbers) == 1 and not numbers[0].blocking
    assert result.insight.self_check.iterations == 3
    assert len(trace.checks) == 3 and trace.checks[-1].passed
    assert len(llm.messages) == 3


async def test_other_problems_are_not_salvaged() -> None:
    db = with_figures(Database())
    value = with_bad_risk(db)
    value["sources"][0]["quote"] = "Выдуманная цитата"
    llm = Replies(finish(value), finish(value))
    result = await generate(
        profile(), [db.news, db.bank], db, llm, limits=Limits(max_critic_reviews=2)
    )
    assert result.insight.status == "insufficient_data"
    [gap] = [g for g in result.gaps if g.blocking]
    assert gap.kind is GapKind.VERIFICATION
    assert any(NUMBER_ISSUE in detail for detail in gap.details)
    assert any("не найдены" in detail for detail in gap.details)
    assert len(llm.messages) == 2


async def test_without_model_only_verified_facts_are_shown_with_reason() -> None:
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
                ),
                KeyFact(
                    id="kf2",
                    text="Спрос сохранится до весны",
                    status=FactStatus.FORECAST,
                    fragment_id=fragment.id,
                ),
                KeyFact(
                    id="kf3",
                    text="Отгрузки растут",
                    status=FactStatus.REPORTED,
                    fragment_id=fragment.id,
                    source_says="РЖД",
                ),
            ]
        }
    )
    result = await generate(profile(), [db.news], db, None)
    assert result.insight.status == "insufficient_data"
    assert [s.text for s in result.insight.what_happened] == [
        "Экспорт угля вырос",
        "РЖД: Отгрузки растут",
    ]
    assert all(row.label == "Факт по источнику" for row in result.presentation.facts)
    assert not result.insight.why_it_matters and not result.insight.actions
    [gap] = [g for g in result.gaps if g.blocking]
    assert gap.kind is GapKind.MODEL and "03:00" in (gap.remedy or "")
    assert "подтверждённые факты" in gap.consequence
    assert GeneratedInsight.model_validate_json(result.model_dump_json()) == result


async def test_ready_insight_names_missing_bank_view_and_figures() -> None:
    db = Database()
    value = draft(db)
    value["news_ids"] = [db.news.id, db.bank.id]
    result = await generate(
        profile(), [db.news, db.bank], db, Replies(finish(value), '{"issues":[]}')
    )
    assert result.insight.status == "ready"
    kinds = {g.kind for g in result.gaps}
    assert GapKind.NUMBERS in kinds and GapKind.BANK_VIEW not in kinds
    assert not any(g.blocking for g in result.gaps)
    without_bank = result.insight.model_copy(update={"bank_view": []})
    gaps = data_gaps(
        profile=profile(),
        news=[db.news, db.bank],
        insight=without_bank,
        execution="completed",
        reviews=[],
    )
    bank = [g for g in gaps if g.kind is GapKind.BANK_VIEW]
    assert len(bank) == 1 and not bank[0].blocking
    assert "не являются официальной позицией банка" in bank[0].consequence


def test_short_material_and_company_questions_are_reported() -> None:
    company = profile(foreign_trade=ForeignTrade(has_customs_records=True, countries=["Китай"]))
    news = item("Рубль ослаб", topics=[Topic.FX, Topic.TRADE])
    questions = related_questions(company, [news])
    assert [q.key for q in questions] == ["markets.export_share", "markets.sales_regions"]
    answered = company.model_copy(
        update={
            "features": [
                *company.features,
                feature("knowledge:markets.export_share"),
            ]
        }
    )
    assert [q.key for q in related_questions(answered, [news])] == ["markets.sales_regions"]
    assert related_questions(company, [news.model_copy(update={"classification": None})]) == []


async def test_gaps_in_response_link_questions_and_short_texts() -> None:
    db = Database()
    company = profile(foreign_trade=ForeignTrade(has_customs_records=True))
    short = db.news.model_copy(
        update={"text": "Рубль ослаб.", "classification": item(topics=[Topic.FX]).classification}
    )
    result = await generate(company, [short], db, None)
    by_kind = {g.kind: g for g in result.gaps}
    assert by_kind[GapKind.SOURCE_TEXT].news_ids == [short.id]
    assert by_kind[GapKind.COMPANY_DATA].question_keys == ["markets.export_share"]
    assert result.gaps[0].blocking


async def test_ready_insight_cannot_carry_blocking_gap() -> None:
    db = Database()
    value = draft(db)
    value["news_ids"] = [db.news.id, db.bank.id]
    result = await generate(
        profile(), [db.news, db.bank], db, Replies(finish(value), '{"issues":[]}')
    )
    assert result.insight.status == "ready"
    payload = result.model_dump(mode="json")
    payload["gaps"].append(
        {
            "kind": "model",
            "missing": "Модель недоступна",
            "consequence": "Вывод не сформирован",
            "blocking": True,
        }
    )
    with pytest.raises(ValidationError):
        GeneratedInsight.model_validate(payload)


def with_second_risk(db: Database) -> dict[str, Any]:
    value = draft(db)
    value["news_ids"] = [db.news.id, db.bank.id]
    value["risks"].append(
        {
            "id": "risk2",
            "text": "Компания потеряет ключевых покупателей",
            "origin": "model",
            "source_ids": ["n", "p"],
        }
    )
    return value


DISPUTED = (
    '{"issues":[{"statement_id":"risk2","problem":"unsupported",'
    '"detail":"Потеря покупателей не следует из источника"}]}'
)


async def test_disputed_statements_are_hidden_and_rest_is_verified() -> None:
    db = Database()
    value = with_second_risk(db)
    llm = Replies(finish(value), DISPUTED, finish(value), DISPUTED, '{"issues":[]}')
    result = await generate(
        profile(), [db.news, db.bank], db, llm, limits=Limits(max_critic_reviews=2)
    )
    assert result.insight.status == "ready" and result.execution_status == "completed"
    assert "risk2" not in {s.id for s in result.insight.statements()}
    assert "risk" in {s.id for s in result.insight.risks}
    [gap] = [g for g in result.gaps if g.kind is GapKind.VERIFICATION]
    assert not gap.blocking and "Скрыто выводов: 1" in gap.consequence
    assert gap.details == ["Потеря покупателей не следует из источника"]
    assert not [g for g in result.gaps if g.kind is GapKind.NUMBERS and "Скрыто" in g.consequence]
    assert len(llm.messages) == 5


async def test_general_semantic_objection_is_not_salvaged() -> None:
    db = Database()
    value = with_second_risk(db)
    general = '{"issues":[{"statement_id":null,"problem":"other","detail":"Разбор не о том"}]}'
    llm = Replies(finish(value), general, finish(value), general)
    result = await generate(
        profile(), [db.news, db.bank], db, llm, limits=Limits(max_critic_reviews=2)
    )
    assert result.insight.status == "insufficient_data"
    assert len(llm.messages) == 4


async def test_salvage_that_empties_a_block_is_rejected() -> None:
    db = Database()
    value = with_second_risk(db)
    both = (
        '{"issues":[{"statement_id":"risk","problem":"unsupported","detail":"x"},'
        '{"statement_id":"risk2","problem":"unsupported","detail":"y"}]}'
    )
    llm = Replies(finish(value), both, finish(value), both)
    result = await generate(
        profile(), [db.news, db.bank], db, llm, limits=Limits(max_critic_reviews=2)
    )
    assert result.insight.status == "insufficient_data"
    assert len(llm.messages) == 4


async def test_disputed_headline_falls_back_to_material_title() -> None:
    db = Database()
    value = {**with_second_risk(db), "headline": "Выдуманный акцент заголовка"}
    reply = (
        '{"issues":[{"statement_id":"headline","problem":"contradiction",'
        '"detail":"Заголовок противоречит данным компании"}]}'
    )
    llm = Replies(finish(value), reply, finish(value), reply, '{"issues":[]}')
    result = await generate(
        profile(), [db.news, db.bank], db, llm, limits=Limits(max_critic_reviews=2)
    )
    assert result.insight.status == "ready"
    assert result.insight.headline == db.news.title
    [gap] = [g for g in result.gaps if g.kind is GapKind.VERIFICATION]
    assert "Скрыто выводов: 0; заголовок заменён" in gap.consequence


async def test_disputed_only_action_is_not_salvaged() -> None:
    db = Database()
    value = with_second_risk(db)
    reply = (
        '{"issues":[{"statement_id":"action","problem":"trivial",'
        '"detail":"Действие не даёт ценности"}]}'
    )
    llm = Replies(finish(value), reply, finish(value), reply)
    result = await generate(
        profile(), [db.news, db.bank], db, llm, limits=Limits(max_critic_reviews=2)
    )
    assert result.insight.status == "insufficient_data"
    assert len(llm.messages) == 4


async def test_draft_without_actions_is_still_rejected() -> None:
    db = Database()
    value = with_second_risk(db)
    value["actions"] = []
    report = await InsightCritic(Replies()).review(context(db), value)
    assert any("Действия" in i.detail for i in report.issues)


async def test_statement_with_unknown_source_is_hidden_and_rest_is_verified() -> None:
    db = Database()
    value = with_second_risk(db)
    value["risks"][1]["source_ids"] = ["missing"]
    llm = Replies(finish(value), finish(value), '{"issues":[]}')
    result = await generate(
        profile(), [db.news, db.bank], db, llm, limits=Limits(max_critic_reviews=2)
    )
    assert result.insight.status == "ready"
    assert "risk2" not in {s.id for s in result.insight.statements()}
    [gap] = [g for g in result.gaps if g.kind is GapKind.VERIFICATION]
    assert "Утверждение ссылается на неизвестный источник" in gap.details
    assert len(llm.messages) == 3
