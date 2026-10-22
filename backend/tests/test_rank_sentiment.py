import json
from collections.abc import Sequence
from typing import Any

import pytest

from app.api.feed import build_feed
from app.contracts.feed import Polarity
from app.contracts.news import NewsItem, Topic
from app.contracts.rank import ImpactAssessment, ImpactColor, ImpactCriterionId
from app.llm.errors import BudgetExceededError
from app.llm.models import LLMResult, Message, Task
from app.profile.okved import industry
from app.rank.sentiment import (
    MAX_CARDS,
    RULES_NOTE,
    assess,
    assessment,
    band_of,
    criteria_info,
    prompt,
    rules_impact,
    scale,
    system_prompt,
)
from tests.test_api_feed import material
from tests.test_news_classify import NOW, FakeLLM, result
from tests.test_rank_rules import item, profile

C = ImpactCriterionId


def answer(*items: dict[str, Any]) -> str:
    return json.dumps({"assessments": list(items)}, ensure_ascii=False)


def judged(key: str, **levels: tuple[int, str]) -> dict[str, Any]:
    return {
        "id": key,
        "criteria": {
            name: {"level": level, "reason": why} for name, (level, why) in levels.items()
        },
        "rationale": f"Итог {key}",
        "confidence": "high",
    }


def test_score_is_sum_of_criteria_and_capped() -> None:
    one = assessment(
        {C.COST_OF_MONEY: (-2, "Ставка останется высокой")}, "Долг дорогой", "llm", "high"
    )
    assert one.score == -50 and one.color is ImpactColor.RED and one.title == "Сильный риск"
    assert one.rationale.startswith("-50: стоимость денег существенно хуже (-50)")
    assert len(one.criteria) == 5
    assert {criterion.id for criterion in one.criteria if criterion.level == 0} == {
        C.REVENUE,
        C.COSTS,
        C.MARKET_ACCESS,
        C.REGULATORY,
    }
    many = assessment(
        {C.REVENUE: (2, "a"), C.COSTS: (2, "b"), C.MARKET_ACCESS: (1, "c")}, "—", "llm", "high"
    )
    assert many.score == 100 and many.color is ImpactColor.GREEN
    three = assessment(
        {C.COSTS: (-1, "a"), C.MARKET_ACCESS: (-1, "b"), C.REGULATORY: (-1, "c")}, "—", "llm", "low"
    )
    assert three.score == -60


@pytest.mark.parametrize(
    ("score", "color"),
    [
        (-100, ImpactColor.RED),
        (-50, ImpactColor.RED),
        (-49, ImpactColor.ORANGE),
        (-15, ImpactColor.ORANGE),
        (-14, ImpactColor.GRAY),
        (14, ImpactColor.GRAY),
        (15, ImpactColor.LIGHT_GREEN),
        (49, ImpactColor.LIGHT_GREEN),
        (50, ImpactColor.GREEN),
    ],
)
def test_color_bands(score: int, color: ImpactColor) -> None:
    assert band_of(score)[0] is color


def test_legend_covers_scale_and_criteria() -> None:
    bands = scale()
    assert [band.min_score for band in bands] == [-100, -49, -14, 15, 50]
    assert all(band.hex.startswith("#") for band in bands)
    assert [info.title for info in criteria_info()] == [
        "Выручка",
        "Издержки",
        "Доступ к рынкам",
        "Стоимость денег",
        "Регуляторный риск",
    ]


def test_contract_rejects_inconsistent_score() -> None:
    good = assessment({C.REVENUE: (1, "Спрос растёт")}, "—", "llm", "medium")
    data = good.model_dump()
    with pytest.raises(ValueError, match="сумме вкладов"):
        ImpactAssessment.model_validate({**data, "score": 90})


def test_rules_use_topic_and_sentiment() -> None:
    rate = item(topics=[Topic.KEY_RATE], sentiment="negative")
    result_ = rules_impact(rate, profile())
    found = {criterion.id: criterion for criterion in result_.criteria}
    assert found[C.COST_OF_MONEY].level == -1
    assert "ключевая ставка" in (found[C.COST_OF_MONEY].reason or "")
    assert (result_.score, result_.method, result_.confidence) == (-20, "rules", "low")
    assert RULES_NOTE in result_.rationale


def test_rules_without_direction_stay_neutral() -> None:
    result_ = rules_impact(item(topics=[Topic.FX]), profile())
    assert result_.score == 0 and result_.color is ImpactColor.GRAY
    assert "направление влияния по разбору не определено" in result_.rationale


def test_commodities_are_revenue_for_extraction() -> None:
    oil = item(topics=[Topic.COMMODITIES], sentiment="positive")
    miner = rules_impact(oil, profile(industry=industry("06.10")))
    trader = rules_impact(oil, profile(industry=industry("46.38")))
    assert next(c for c in miner.criteria if c.level).id is C.REVENUE
    assert next(c for c in trader.criteria if c.level).id is C.COSTS


def cards(count: int) -> list[tuple[NewsItem, str]]:
    return [
        (material(f"n:{index}", f"Новость {index}"), "Совпала отрасль") for index in range(count)
    ]


async def test_model_assesses_cards_and_rules_fill_the_rest() -> None:
    llm = FakeLLM(
        answer(
            judged("n1", cost_of_money=(-2, "Кредиты подорожают"), revenue=(1, "Спрос вырастет")),
            judged("n2", unknown=(2, "лишний критерий")),
        )
    )
    result_ = await assess(llm, profile(), cards(3))
    first, second, third = (result_.impacts[f"n:{index}"] for index in range(3))
    assert (first.score, first.method, first.model) == (-30, "llm", "deepseek-v4.1-flash")
    assert second.score == 0 and second.method == "llm"
    assert third.method == "rules"
    assert llm.tasks == [Task.INTERPRET]
    assert "<<<НОВОСТИ" in llm.calls[0][1].content


async def test_level_without_reason_triggers_repair() -> None:
    bad = answer(judged("n1", costs=(-1, "")))
    good = answer(judged("n1", costs=(-1, "Закупки подорожают")))
    llm = FakeLLM(bad, good)
    result_ = await assess(llm, profile(), cards(1))
    assert result_.impacts["n:0"].score == -20
    assert "Нет причины" in llm.calls[1][-1].content


async def test_failure_falls_back_to_rules_with_error() -> None:
    result_ = await assess(FakeLLM(BudgetExceededError("Бюджет исчерпан")), profile(), cards(2))
    assert all(found.method == "rules" for found in result_.impacts.values())
    assert result_.error is not None
    none = await assess(None, profile(), cards(2))
    assert none.error is None and none.model is None


async def test_only_first_cards_go_to_model() -> None:
    llm = FakeLLM(answer())
    result_ = await assess(llm, profile(), cards(MAX_CARDS + 2))
    assert len(result_.impacts) == MAX_CARDS + 2
    assert f"[n{MAX_CARDS}]" in llm.calls[0][1].content
    assert f"[n{MAX_CARDS + 1}]" not in llm.calls[0][1].content


class MemoryCache:
    def __init__(self) -> None:
        self.saved: dict[tuple[str, str, str], tuple[dict[str, Any], str]] = {}

    async def get(self, kind: str, version: str, prompt: str) -> Any:
        found = self.saved.get((kind, version, prompt))
        return None if found is None else type("Hit", (), {"result": found[0], "model": found[1]})()

    async def put(
        self, kind: str, version: str, prompt: str, result: dict[str, Any], model: str, cost: Any
    ) -> None:
        self.saved[(kind, version, prompt)] = (result, model)


async def test_cached_assessment_skips_model() -> None:
    cache = MemoryCache()
    first = FakeLLM(answer(judged("n1", regulatory=(-1, "Новые требования"))))
    await assess(first, profile(), cards(1), cache)  # type: ignore[arg-type]
    down = FakeLLM(BudgetExceededError("Бюджет исчерпан"))
    again = await assess(down, profile(), cards(1), cache)  # type: ignore[arg-type]
    assert again.cached and down.calls == []
    assert again.impacts["n:0"].method == "llm"


def test_prompt_lists_criteria_and_marks_news_as_data() -> None:
    text = system_prompt()
    assert all(key.value in text for key in C)
    assert "игнорируй" in text
    user = prompt(profile(), cards(1))
    assert "Почему отобрана: Совпала отрасль" in user
    assert user.endswith("НОВОСТИ>>>")


class ByTask:
    """Модель-заглушка: на проверку кандидатов подтверждает, на оценку влияния — оценивает."""

    def __init__(self) -> None:
        self.tasks: list[Task] = []

    async def complete(self, task: Task, messages: Sequence[Message]) -> LLMResult:
        self.tasks.append(task)
        if task is Task.RERANK:
            verdict = {"id": "n1", "relevant": True, "score": 0.9, "reason": "Касается рынка"}
            return result(json.dumps({"verdicts": [verdict]}, ensure_ascii=False))
        return result(answer(judged("n1", revenue=(2, "Рынок растёт"))))


async def test_feed_card_carries_impact() -> None:
    oil = material("oil", "Нефть дорожает", segments=["oil_gas"])
    llm = ByTask()
    feed = await build_feed(profile(), [oil], llm, now=NOW)
    card = feed.items[0]
    assert (card.impact.score, card.impact.method) == (50, "llm")
    assert card.event.polarity is Polarity.OPPORTUNITY
    assert llm.tasks == [Task.RERANK, Task.INTERPRET]
    assert len(feed.impact_scale) == 5 and len(feed.impact_criteria) == 5
    plain = await build_feed(profile(), [oil], None, now=NOW)
    assert plain.items[0].impact.method == "rules"
