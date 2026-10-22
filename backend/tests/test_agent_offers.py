from typing import Any

import pytest
from pydantic import ValidationError

from app.agent.critic import InsightCritic
from app.agent.insight import generate
from app.agent.offers import DISCLAIMER, candidates, offer_for, product_issues, prompt_hint
from app.agent.tools import Decision
from app.contracts.common import BankProduct, Significance
from app.contracts.generation import GeneratedInsight, OfferAction
from app.contracts.insight import Action, ActionKind, Origin
from app.contracts.news import Classification, NewsItem, Topic
from app.contracts.profile import CompanyProfile, Exposure, ExposureDirection
from app.profile.okved import industry
from tests.test_agent_builtin_tools import Database
from tests.test_agent_critic import context, draft
from tests.test_agent_tools import Replies
from tests.test_rank_rules import item, profile


def borrower(**changes: Any) -> CompanyProfile:
    base: dict[str, Any] = {
        "products": [BankProduct.LOAN],
        "exposures": [
            Exposure(
                topic=Topic.KEY_RATE,
                direction=ExposureDirection.RISK,
                reason="Кредиты в банке",
                feature_ids=["feature:debt_dependence", "feature:loan"],
            )
        ],
    }
    return profile(**{**base, **changes})


def about(*topics: Topic) -> NewsItem:
    return item("Ставка сохранена", topics=list(topics))


def action(product: BankProduct, id_: str = "offer") -> Action:
    return Action(
        id=id_,
        text="Обсудить продукт с менеджером",
        kind=ActionKind.BANK_PRODUCT,
        product=product,
        origin=Origin.MODEL,
        source_ids=["n"],
    )


def test_products_follow_event_topic_and_company_grounds() -> None:
    rate = about(Topic.KEY_RATE)
    found = {rule.product: features for rule, features in candidates(borrower(), [rate])}
    assert found[BankProduct.LOAN] == ["feature:debt_dependence", "feature:loan"]
    assert found[BankProduct.DEPOSIT] == ["feature:deposit"]
    assert BankProduct.GUARANTEE not in found
    assert candidates(borrower(), [about(Topic.LABOR)]) == []
    assert candidates(profile(features=["industry"]), [rate]) == []
    carrier = profile(features=["industry"], industry=industry("49.41"))
    assert [r.product for r, _ in candidates(carrier, [about(Topic.LOGISTICS)])] == [
        BankProduct.LEASING
    ]


def test_prompt_lists_only_grounded_products() -> None:
    hint = prompt_hint(borrower(), [about(Topic.KEY_RATE)])
    assert "loan" in hint and "deposit" in hint and "guarantee" not in hint
    assert "не больше одного" in hint
    assert "не предлагай" in prompt_hint(borrower(), [about(Topic.LABOR)])


def test_unfounded_or_repeated_products_are_rejected() -> None:
    company, news = borrower(), [about(Topic.KEY_RATE)]
    assert product_issues([action(BankProduct.LOAN)], company, news) == []
    [wrong] = product_issues([action(BankProduct.GUARANTEE)], company, news)
    assert wrong.statement_id == "offer" and "guarantee" in wrong.detail
    many = product_issues(
        [action(BankProduct.LOAN, "a"), action(BankProduct.DEPOSIT, "b")], company, news
    )
    assert [issue.detail for issue in many] == ["Не больше одного продукта банка на инсайт"]


async def test_critic_blocks_product_without_grounds_before_llm() -> None:
    db = Database()
    value = draft(db)
    value["actions"].append(
        {
            "id": "sell",
            "text": "Оформите гарантию",
            "kind": "bank_product",
            "product": "guarantee",
            "origin": "model",
            "source_ids": ["n"],
        }
    )
    llm = Replies()
    report = await InsightCritic(llm).review(context(db), value)
    assert not report.passed and not llm.messages
    assert any(issue.statement_id == "sell" for issue in report.issues)


def rate_case(db: Database) -> tuple[list[NewsItem], dict[str, Any]]:
    news = db.news.model_copy(update={"classification": Classification(topics=[Topic.KEY_RATE])})
    db.news = news
    value = draft(db)
    value["news_ids"] = [db.news.id, db.bank.id]
    value["sources"].append(
        {
            "id": "d",
            "type": "profile_feature",
            "ref_id": "feature:debt_dependence",
            "title": "Зависимость от заёмных средств",
        }
    )
    value["risks"][0]["source_ids"] = ["n", "d"]
    return [db.news, db.bank], value


def finish(value: dict[str, Any]) -> str:
    return Decision(action="finish", reason="Готово", output=value).model_dump_json()


async def test_ready_insight_gets_one_grounded_offer() -> None:
    db = Database()
    news, value = rate_case(db)
    llm = Replies(finish(value), '{"issues":[]}')
    result = await generate(borrower(), news, db, llm)
    assert result.insight.status == "ready"
    assert "Уместные продукты банка: loan" in llm.messages[0][1].content
    insight = result.insight.model_copy(update={"significance": Significance.HIGH})
    offer = offer_for(insight, borrower(), news)
    assert offer is not None
    assert offer.product is BankProduct.LOAN
    assert offer.statement_ids == ["risk"]
    assert offer.already_client and "пересмотре условий" in offer.benefit
    assert offer.next_actions == [OfferAction.CONTACT_MANAGER, OfferAction.CALLBACK]
    assert "Зависимость от заёмных средств" in offer.why or "debt_dependence" in offer.why
    assert offer.disclaimer == DISCLAIMER and "инвестиционной рекомендацией" in offer.disclaimer
    final = GeneratedInsight.model_validate(
        {
            **result.model_dump(mode="json"),
            "insight": insight.model_dump(mode="json"),
            "offer": offer.model_dump(mode="json"),
        }
    )
    assert final.offer == offer
    new_client = offer_for(insight, borrower(products=[]), news)
    assert new_client is not None and OfferAction.APPLICATION in new_client.next_actions


async def test_no_offer_without_grounds_significance_or_ready_insight() -> None:
    db = Database()
    news, value = rate_case(db)
    result = await generate(borrower(), news, db, Replies(finish(value), '{"issues":[]}'))
    ready = result.insight.model_copy(update={"significance": Significance.HIGH})
    low = ready.model_copy(update={"significance": Significance.LOW})
    assert offer_for(low, borrower(), news) is None
    no_exposure = borrower(exposures=[])
    plain = ready.model_copy(
        update={"risks": [r.model_copy(update={"source_ids": ["n"]}) for r in ready.risks]}
    )
    assert offer_for(plain, no_exposure, news) is None
    assert offer_for(plain, borrower(), news) is not None
    unavailable = await generate(borrower(), news, db, None)
    assert unavailable.offer is None
    assert offer_for(ready, borrower(), [about(Topic.LABOR)]) is None


async def test_contract_keeps_offer_tied_to_ready_insight() -> None:
    db = Database()
    news, value = rate_case(db)
    result = await generate(borrower(), news, db, Replies(finish(value), '{"issues":[]}'))
    ready = result.insight.model_copy(update={"significance": Significance.HIGH})
    offer = offer_for(ready, borrower(), news)
    assert offer is not None
    payload = {**result.model_dump(mode="json"), "insight": ready.model_dump(mode="json")}
    broken = offer.model_copy(update={"statement_ids": ["action"]})
    with pytest.raises(ValidationError):
        GeneratedInsight.model_validate({**payload, "offer": broken.model_dump(mode="json")})
    failed = await generate(borrower(), news, db, None)
    with pytest.raises(ValidationError):
        GeneratedInsight.model_validate(
            {**failed.model_dump(mode="json"), "offer": offer.model_dump(mode="json")}
        )
