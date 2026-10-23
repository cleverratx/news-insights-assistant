import math

import pytest
from pydantic import ValidationError

from app.api.feed import build_feed
from app.contracts.news import MaterialKind, Topic
from app.contracts.rank import ImportanceLevel
from app.rank.importance import importance
from app.rank.roles import Role, RoleWeights, apply_role, weights_for
from app.rank.score import score
from tests.test_api_feed import answer, material
from tests.test_news_classify import NOW, FakeLLM
from tests.test_rank_rules import item, profile


def test_role_changes_same_event_without_mutating_company_score() -> None:
    news = item(topics=[Topic.KEY_RATE], segments=["oil_gas"])
    company = profile()
    original = score(company, news, NOW)
    financial = apply_role(original, news, weights_for(Role.CFO))
    logistics = apply_role(original, news, weights_for(Role.LOGISTICS))
    assert financial.relevance > original.relevance > logistics.relevance
    assert original == score(company, news, NOW)
    assert company == profile()
    assert financial.role is not None
    assert financial.role.company_relevance == original.relevance
    assert sum(f.contribution or 0 for f in financial.contributions) == pytest.approx(
        financial.final, abs=0.001
    )


def test_irrelevant_news_cannot_be_promoted() -> None:
    news = item(topics=[Topic.KEY_RATE], segments=["retail"])
    adjusted = apply_role(score(profile(), news, NOW), news, weights_for(Role.CFO))
    assert adjusted.relevance == adjusted.final == 0
    assert adjusted.contributions == []


def test_empty_weights_preserve_company_score() -> None:
    news = item(segments=["oil_gas"])
    original = score(profile(), news, NOW)
    adjusted = apply_role(original, news, RoleWeights(id="custom", title="Новая роль"))
    assert adjusted.final == original.final
    assert adjusted.relevance == original.relevance
    assert adjusted.contributions == original.contributions


@pytest.mark.parametrize("value", [-1, 0, 2.1, math.nan, math.inf])
def test_custom_weights_are_bounded(value: float) -> None:
    with pytest.raises(ValidationError):
        RoleWeights(id="custom", title="Роль", topic_weights={Topic.FX: value})


def test_custom_feature_weight_changes_existing_match_only() -> None:
    news = item(segments=["oil_gas"])
    original = score(profile(), news, NOW)
    weights = RoleWeights(
        id="custom",
        title="Снабжение",
        feature_weights={"feature:industry": 0.5, "missing": 2},
    )
    adjusted = apply_role(original, news, weights)
    assert adjusted.relevance == pytest.approx(original.relevance / 2, abs=0.0001)
    assert {f.feature_id for f in adjusted.contributions} == {
        f.feature_id for f in original.contributions
    }


async def test_roles_reverse_order_and_apply_threshold_without_llm() -> None:
    rate = material("news:rate", "Ставка", topics=[Topic.KEY_RATE], segments=["oil_gas"])
    shipping = material(
        "news:shipping", "Перевозки", topics=[Topic.LOGISTICS], segments=["oil_gas"]
    )
    cfo = await build_feed(profile(), [rate, shipping], None, now=NOW, role=weights_for(Role.CFO))
    logistics = await build_feed(
        profile(), [rate, shipping], None, now=NOW, role=weights_for(Role.LOGISTICS)
    )
    assert cfo.items[0].news_id == rate.id
    assert logistics.items[0].news_id == shipping.id
    assert logistics.hidden_below_threshold == 1
    assert logistics.role == "logistics"
    assert logistics.items[0].score.role is not None
    assert "Роль" in logistics.items[0].importance.factors[0].detail
    assert not logistics.verified


async def test_model_confirmation_preserves_role_and_contribution_totals() -> None:
    news = material("news:rate", "Ставка", topics=[Topic.KEY_RATE], segments=["oil_gas"])
    result = await build_feed(
        profile(),
        [news],
        FakeLLM(answer(("n1", True, 0.9))),
        now=NOW,
        role=weights_for(Role.CFO),
    )
    [shown] = result.items
    assert result.verified and shown.score.role is not None
    assert sum(f.contribution or 0 for f in shown.features) == pytest.approx(
        shown.score.final, abs=0.001
    )


def test_same_event_is_critical_for_finance_and_background_for_logistics() -> None:
    news = item(topics=[Topic.KEY_RATE], segments=["oil_gas"], regions=["Свердловская область"])
    original = score(profile(), news, NOW)
    finance = importance(apply_role(original, news, weights_for(Role.CFO)), MaterialKind.NEWS)
    logistics = importance(
        apply_role(original, news, weights_for(Role.LOGISTICS)), MaterialKind.NEWS
    )
    assert finance.level is ImportanceLevel.CRITICAL
    assert logistics.level is ImportanceLevel.BACKGROUND


def test_multiple_feature_weights_redistribute_contributions() -> None:
    news = item(segments=["oil_gas"], regions=["Свердловская область"])
    original = score(profile(), news, NOW)
    adjusted = apply_role(
        original,
        news,
        RoleWeights(
            id="regional",
            title="Региональный директор",
            feature_weights={"feature:industry": 0.2, "feature:region": 2},
        ),
    )
    before = {f.feature_id: f.contribution or 0 for f in original.contributions}
    after = {f.feature_id: f.contribution or 0 for f in adjusted.contributions}
    assert after["feature:region"] / after["feature:industry"] > (
        before["feature:region"] / before["feature:industry"]
    )
    assert sum(after.values()) == pytest.approx(adjusted.final, abs=0.001)


def test_plural_forms_and_decimal_comma() -> None:
    from app.core.words import decimal, plural

    forms = ("компания", "компании", "компаний")
    assert [plural(n, *forms) for n in (1, 2, 5, 11, 21, 22, 111)] == [
        "компания",
        "компании",
        "компаний",
        "компаний",
        "компания",
        "компании",
        "компаний",
    ]
    assert decimal(1.6) == "1,60"
