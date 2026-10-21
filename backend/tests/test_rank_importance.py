from datetime import timedelta

import pytest

from app.api.feed import build_feed
from app.contracts.common import BankProduct, Significance
from app.contracts.news import MaterialKind, Topic
from app.contracts.profile import Exposure, ExposureDirection
from app.contracts.rank import Importance, ImportanceFactor, ImportanceLevel
from app.core.config import Settings
from app.rank.importance import (
    BANK_MAX,
    DEFAULT_MIN_IMPORTANCE,
    NO_LINK,
    RELEVANCE_MAX,
    importance,
    level_of,
    scale,
)
from app.rank.score import score
from tests.test_api_feed import material
from tests.test_news_classify import NOW
from tests.test_rank_rules import item, profile


def factors(result: Importance) -> dict[str, ImportanceFactor]:
    return {factor.id: factor for factor in result.factors}


def test_factors_add_up_to_score_and_explain_it() -> None:
    scored = score(profile(), item(segments=["oil_gas"]), NOW)
    result = importance(scored, MaterialKind.NEWS)
    found = factors(result)
    assert list(found) == ["relevance", "scale", "recency", "breadth", "model", "bank"]
    assert sum(factor.max_points for factor in result.factors) == 10
    assert result.score == round(sum(factor.points for factor in result.factors), 1)
    assert found["relevance"].points == round(scored.relevance * RELEVANCE_MAX, 1)
    assert "Нефть, газ" in found["relevance"].detail
    assert found["recency"].points == 1.5 and "свежесть 100%" in found["recency"].detail
    assert found["model"].points == 0 and "не проверяла" in found["model"].detail
    assert found["bank"].points == 0
    assert found["breadth"].detail == "Задето: отрасль"


def test_model_bank_and_breadth_raise_importance() -> None:
    exposures = [
        Exposure(
            topic=Topic.KEY_RATE,
            direction=ExposureDirection.RISK,
            reason="Высокая долговая нагрузка",
            feature_ids=["feature:debt_dependence"],
        )
    ]
    company = profile(products=[BankProduct.LOAN], exposures=exposures)
    scored = score(company, item(topics=[Topic.KEY_RATE], segments=["oil_gas"]), NOW)
    plain = importance(scored, MaterialKind.NEWS)
    rich = importance(scored, MaterialKind.BANK_ANALYTICS, llm=0.9)
    assert factors(plain)["breadth"].points == 1.0
    assert "продукты банка" in factors(plain)["breadth"].detail
    assert factors(rich)["model"].points == 0.9
    assert factors(rich)["bank"].points == BANK_MAX
    assert rich.score == pytest.approx(plain.score + 0.9 + BANK_MAX, abs=0.051)


def test_loud_news_without_link_stays_background() -> None:
    scored = score(profile(), item(topics=[Topic.KEY_RATE], segments=["retail"]), NOW)
    result = importance(scored, MaterialKind.BANK_ANALYTICS, llm=1.0)
    assert result.score == 0
    assert result.level is ImportanceLevel.BACKGROUND
    assert all(factor.detail == NO_LINK for factor in result.factors[1:])


def test_levels_and_scale_for_user() -> None:
    assert level_of(7.5)[0] is ImportanceLevel.CRITICAL
    assert level_of(7.4)[0] is ImportanceLevel.IMPORTANT
    assert level_of(5.5) == (ImportanceLevel.IMPORTANT, "Важно")
    assert level_of(3.5)[1] == "К сведению"
    assert level_of(3.4)[0] is ImportanceLevel.BACKGROUND
    legend = scale()
    assert [entry.title for entry in legend] == ["Критично", "Важно", "К сведению", "Фон"]
    assert all(entry.description for entry in legend)


def test_contract_rejects_factors_that_do_not_add_up() -> None:
    factor = ImportanceFactor(id="relevance", title="Связь", points=2, max_points=4, detail="—")
    with pytest.raises(ValueError, match="Сумма вкладов"):
        Importance(score=5, level=ImportanceLevel.IMPORTANT, title="Важно", factors=[factor])


def test_default_threshold_matches_settings() -> None:
    assert Settings().feed_min_importance == DEFAULT_MIN_IMPORTANCE


async def test_feed_orders_by_importance_and_hides_below_threshold() -> None:
    oil = material("oil", "Нефть", segments=["oil_gas"])
    stale = material("stale", "Регион", age=timedelta(days=6), regions=["Свердловская область"])
    result = await build_feed(profile(), [stale, oil], None, now=NOW)
    shown = [card.importance.score for card in result.items]
    assert shown == sorted(shown, reverse=True)
    assert result.min_importance == DEFAULT_MIN_IMPORTANCE
    assert result.importance_scale[0].title == "Критично"
    strict = await build_feed(profile(), [stale, oil], None, now=NOW, min_importance=10)
    assert strict.items == [] and strict.hidden_below_threshold == 2
    loose = await build_feed(profile(), [stale, oil], None, now=NOW, min_importance=0)
    assert loose.hidden_below_threshold == 0 and len(loose.items) == 2


async def test_card_significance_follows_importance_level() -> None:
    result = await build_feed(
        profile(), [material("oil", "Нефть", segments=["oil_gas"])], None, now=NOW
    )
    card = result.items[0]
    expected = {
        ImportanceLevel.CRITICAL: Significance.HIGH,
        ImportanceLevel.IMPORTANT: Significance.HIGH,
        ImportanceLevel.INFO: Significance.MEDIUM,
        ImportanceLevel.BACKGROUND: Significance.LOW,
    }[card.importance.level]
    assert card.significance is expected
    assert card.importance.factors[0].id == "relevance"
