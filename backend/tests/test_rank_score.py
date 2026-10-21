from datetime import timedelta

import pytest

from app.contracts.common import BankProduct, Significance
from app.contracts.news import FactStatus, Horizon, KeyFact, MaterialKind, Topic
from app.contracts.profile import Exposure, ExposureDirection
from app.contracts.rank import RankStage
from app.rank.rules import INDUSTRY_SEGMENT, SCALE_MISMATCH, Matching, Rule, RuleHit
from app.rank.score import (
    RECENCY_SHARE,
    RELEVANCE_SHARE,
    SCALE_SHARE,
    TOPIC_SCALE,
    recency,
    scale,
    score,
)
from tests.test_news_classify import NOW
from tests.test_rank_rules import item, profile


def test_irrelevant_news_scores_zero() -> None:
    result = score(profile(), item(segments=["retail"]), NOW)
    assert result.final == 0
    assert result.contributions == []
    assert result.factors[0] == "Не совпадает ни с одним признаком компании"
    assert result.significance is Significance.LOW


def test_final_combines_relevance_recency_and_scale() -> None:
    result = score(profile(), item(segments=["oil_gas"]), NOW)
    assert result.relevance == INDUSTRY_SEGMENT
    assert result.recency == 1
    assert result.scale == TOPIC_SCALE[Topic.INDUSTRY]
    modifier = RELEVANCE_SHARE + RECENCY_SHARE + SCALE_SHARE * result.scale
    assert result.final == pytest.approx(INDUSTRY_SEGMENT * modifier, abs=1e-4)


@pytest.mark.parametrize(
    ("horizon", "hours"),
    [(Horizon.DAYS, 24), (Horizon.WEEKS, 72), (Horizon.MONTHS, 168), (Horizon.YEARS, 336)],
)
def test_recency_half_life_follows_horizon(horizon: Horizon, hours: int) -> None:
    news = item(horizon=horizon)
    value, note = recency(news, NOW + timedelta(hours=hours))
    assert value == 0.5
    assert "свежесть 50%" in note


def test_bank_analytics_ages_slower_and_future_is_fresh() -> None:
    news = item(horizon=Horizon.DAYS)
    bank = news.model_copy(update={"kind": MaterialKind.BANK_ANALYTICS})
    later = NOW + timedelta(hours=24)
    assert recency(bank, later)[0] > recency(news, later)[0]
    assert recency(news, NOW - timedelta(hours=5)) == (
        1,
        "Опубликовано меньше часа назад, свежесть 100%",
    )
    assert "3 дн. назад" in recency(news, NOW + timedelta(days=3))[1]


def test_scale_grows_with_coverage_and_confirmed_numbers() -> None:
    news = item(topics=[Topic.COMPANY, Topic.KEY_RATE])
    base, note = scale(news)
    assert base == TOPIC_SCALE[Topic.KEY_RATE]
    assert "ключевая ставка" in note
    fact = KeyFact(id="f1", text="Ставка 14%", status=FactStatus.CONFIRMED, numbers=["14%"])
    louder = news.model_copy(update={"key_facts": [fact]})
    value, note = scale(louder, coverage=5)
    assert value == 1
    assert "пишут 5 изданий" in note and "подтверждённые цифры" in note
    guess = fact.model_copy(update={"status": FactStatus.FORECAST})
    assert scale(news.model_copy(update={"key_facts": [guess]}))[0] == base


def test_contributions_explain_final_score() -> None:
    exposures = [
        Exposure(
            topic=Topic.KEY_RATE,
            direction=ExposureDirection.RISK,
            reason="Зависимость от заёмных средств высокая",
            feature_ids=["feature:debt_dependence", "feature:loan"],
        )
    ]
    company = profile(products=[BankProduct.LOAN], exposures=exposures)
    result = score(company, item(topics=[Topic.KEY_RATE]), NOW)
    assert [found.feature_id for found in result.contributions] == [
        "feature:loan",
        "feature:debt_dependence",
    ]
    loan, debt = result.contributions
    assert loan.rule == Rule.PRODUCT and debt.rule == Rule.EXPOSURE
    assert loan.contribution is not None and debt.contribution is not None
    assert loan.contribution > debt.contribution
    assert loan.contribution + debt.contribution == pytest.approx(result.final, abs=1e-3)


def test_penalty_is_explained() -> None:
    news = item("Льготные кредиты для МСП продлят", segments=["oil_gas"])
    result = score(profile(), news, NOW)
    assert result.relevance == pytest.approx(INDUSTRY_SEGMENT * SCALE_MISMATCH)
    assert any("малого и среднего" in factor for factor in result.factors)


def test_ranked_item_carries_breakdown() -> None:
    hits = [RuleHit(Rule.INDUSTRY, 0.95, "feature:industry", "Свой рынок")]
    result = score(profile(), item(segments=["retail"]), NOW, matching=Matching(hits=hits))
    ranked = result.ranked(rank=1, run_id="run:1")
    assert ranked.stage is RankStage.CANDIDATE
    assert ranked.significance is Significance.HIGH
    assert ranked.score.final == result.final
    assert ranked.score.relevance == 0.95
    assert len(ranked.score.factors) == 2
    assert ranked.matched_features[0].contribution == result.final
