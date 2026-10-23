import json
from collections.abc import Sequence
from typing import Any

import pytest

from app.api.feed import build_feed
from app.contracts.feed import Polarity
from app.contracts.news import NewsItem, Topic
from app.contracts.rank import (
    ImpactAssessment,
    ImpactColor,
    ImpactCriterionId,
    ImpactTiming,
)
from app.llm.errors import BudgetExceededError
from app.llm.models import LLMResult, Message, Task
from app.profile.okved import industry
from app.rank.sentiment import (
    MAX_CARDS,
    RULES_NOTE,
    Effect,
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


FIRST_QUESTION = {
    "revenue": "q1",
    "costs": "q7",
    "cost_of_money": "q14",
    "liquidity": "q19",
    "fx": "q24",
    "counterparties": "q28",
    "competition": "q33",
    "market_access": "q38",
    "regulatory": "q43",
    "unknown": "q99",
}


def judged(key: str, **levels: tuple[str | int, ...]) -> dict[str, Any]:
    """Ответ модели: имя критерия или номер вопроса (q8) → уровень, причина, срок."""
    answers = []
    for name, value in levels.items():
        number = FIRST_QUESTION.get(name, name)
        answer: dict[str, Any] = {"q": number, "level": value[0], "reason": value[1]}
        if len(value) == 4:
            answer.update(when=value[2], when_from=value[3])
        answers.append(answer)
    return {"id": key, "answers": answers, "rationale": f"Итог {key}", "confidence": "high"}


def test_score_is_sum_of_criteria_and_capped() -> None:
    one = assessment(
        {C.COST_OF_MONEY: Effect(-2, "Ставка останется высокой")}, "Долг дорогой", "llm", "high"
    )
    assert one.score == -2 and one.color is ImpactColor.ORANGE and one.title == "Риск"
    assert one.rationale.startswith("-2: стоимость денег существенно хуже (-2)")
    assert one.scale == "levels"
    assert len(one.criteria) == len(C)
    assert {criterion.id for criterion in one.criteria if criterion.level == 0} == set(C) - {
        C.COST_OF_MONEY
    }
    many = assessment(
        {C.REVENUE: Effect(2, "a"), C.COSTS: Effect(2, "b"), C.MARKET_ACCESS: Effect(1, "c")},
        "—",
        "llm",
        "high",
    )
    assert many.score == 5 and many.color is ImpactColor.GREEN
    three = assessment(
        {C.COSTS: Effect(-1, "a"), C.FX: Effect(-1, "b"), C.LIQUIDITY: Effect(-1, "c")},
        "—",
        "llm",
        "low",
    )
    assert three.score == -3


@pytest.mark.parametrize(
    ("score", "color"),
    [
        (-100, ImpactColor.RED),
        (-4, ImpactColor.RED),
        (-3, ImpactColor.ORANGE),
        (-1, ImpactColor.ORANGE),
        (0, ImpactColor.GRAY),
        (1, ImpactColor.LIGHT_GREEN),
        (3, ImpactColor.LIGHT_GREEN),
        (4, ImpactColor.GREEN),
        (100, ImpactColor.GREEN),
    ],
)
def test_color_bands(score: int, color: ImpactColor) -> None:
    assert band_of(score)[0] is color


def test_legend_covers_scale_and_criteria() -> None:
    bands = scale()
    assert [band.min_score for band in bands] == [-100, -3, 0, 1, 4]
    assert all(band.hex.startswith("#") for band in bands)
    assert [info.title for info in criteria_info()] == [
        "Выручка и спрос",
        "Издержки",
        "Стоимость денег",
        "Ликвидность и оборотный капитал",
        "Валютный риск",
        "Контрагенты и цепочка поставок",
        "Конкурентная позиция",
        "Доступ к рынкам",
        "Регуляторика и налоги",
    ]


def test_contract_rejects_inconsistent_score() -> None:
    good = assessment({C.REVENUE: Effect(1, "Спрос растёт")}, "—", "llm", "medium")
    data = good.model_dump()
    with pytest.raises(ValueError, match="сумме вкладов"):
        ImpactAssessment.model_validate({**data, "score": 90})


def test_rules_use_topic_and_sentiment() -> None:
    rate = item(topics=[Topic.KEY_RATE], sentiment="negative")
    result_ = rules_impact(rate, profile())
    found = {criterion.id: criterion for criterion in result_.criteria}
    assert found[C.COST_OF_MONEY].level == -1
    assert "ключевая ставка" in (found[C.COST_OF_MONEY].reason or "")
    assert (result_.score, result_.method, result_.confidence) == (-1, "rules", "low")
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
            judged("n1", cost_of_money=(-1, "Кредиты подорожают"), fx=(2, "Рубль окрепнет")),
            judged("n2", unknown=(2, "лишний критерий")),
        )
    )
    result_ = await assess(llm, profile(), cards(3))
    first, second, third = (result_.impacts[f"n:{index}"] for index in range(3))
    assert (first.score, first.method, first.model) == (0, "llm", "deepseek-v4.1-flash")
    found = {criterion.id: criterion.level for criterion in first.criteria}
    assert (found[C.COST_OF_MONEY], found[C.FX]) == (-1, 1)
    assert second.score == 0 and second.method == "llm"
    assert third.method == "rules"
    assert llm.tasks == [Task.INTERPRET]
    assert "<<<НОВОСТИ" in llm.calls[0][1].content


async def test_level_without_reason_triggers_repair() -> None:
    bad = answer(judged("n1", costs=(-1, "")))
    good = answer(judged("n1", costs=(-1, "Закупки подорожают")))
    llm = FakeLLM(bad, good)
    result_ = await assess(llm, profile(), cards(1))
    assert result_.impacts["n:0"].score == -1
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
    assert all(f"{info.title}:" in text for info in criteria_info())
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
        return result(answer(judged("n1", revenue=(2, "Нефть дорожает на 12%"))))


async def test_feed_card_carries_impact() -> None:
    oil = material("oil", "Нефть дорожает на 12%", segments=["oil_gas"])
    llm = ByTask()
    feed = await build_feed(profile(), [oil], llm, now=NOW)
    card = feed.items[0]
    assert (card.impact.score, card.impact.method) == (2, "llm")
    assert card.event.polarity is Polarity.OPPORTUNITY
    assert llm.tasks == [Task.RERANK, Task.INTERPRET]
    assert len(feed.impact_scale) == 5 and len(feed.impact_criteria) == len(C)
    plain = await build_feed(profile(), [oil], None, now=NOW)
    assert plain.items[0].impact.method == "rules"


def priced(title: str) -> list[tuple[NewsItem, str]]:
    return [(material("n:0", title), "Совпала отрасль")]


async def test_strong_level_needs_a_figure_from_news_or_company() -> None:
    bare = FakeLLM(answer(judged("n1", cost_of_money=(-2, "Кредиты подорожают"))))
    weak = (await assess(bare, profile(), priced("Ставка 14% сохранена"))).impacts["n:0"]
    assert weak.score == -1
    backed = FakeLLM(answer(judged("n1", cost_of_money=(-2, "Ставка 14% удержит дорогой долг"))))
    strong = (await assess(backed, profile(), priced("Ставка 14% сохранена"))).impacts["n:0"]
    assert strong.score == -2
    invented = FakeLLM(answer(judged("n1", cost_of_money=(-2, "Ставка 17% удорожит долг"))))
    capped = (await assess(invented, profile(), priced("Ставка 14% сохранена"))).impacts["n:0"]
    assert capped.score == -1


async def test_model_answers_only_affected_criteria() -> None:
    llm = FakeLLM(answer(judged("n1", liquidity=(-1, "Покупатели затянут оплату"))))
    found = (await assess(llm, profile(), cards(1))).impacts["n:0"]
    assert len(found.criteria) == len(C)
    assert [c.id for c in found.criteria if c.level] == [C.LIQUIDITY]


async def test_timing_from_source_needs_the_date_in_news() -> None:
    title = "Пошлина вырастет с 2027 года"
    stated = FakeLLM(
        answer(judged("n1", market_access=(-1, "Пошлина с 2027 года", "later", "source")))
    )
    impact = (await assess(stated, profile(), priced(title))).impacts["n:0"]
    [moved] = [c for c in impact.criteria if c.level]
    assert (moved.timing, moved.timing_source) == (ImpactTiming.LATER, "source")
    assert moved.timing_title == "больше чем через год"
    claimed = FakeLLM(answer(judged("n1", costs=(-1, "Закупки подорожают", "quarter", "source"))))
    impact = (await assess(claimed, profile(), priced(title))).impacts["n:0"]
    [moved] = [c for c in impact.criteria if c.level]
    assert (moved.timing, moved.timing_source) == (ImpactTiming.QUARTER, "estimate")


async def test_missing_timing_falls_back_to_news_horizon() -> None:
    news = material("n:0", "Ставка сохранена", horizon="months")
    llm = FakeLLM(answer(judged("n1", cost_of_money=(-1, "Долг останется дорогим"))))
    impact = (await assess(llm, profile(), [(news, "Совпала отрасль")])).impacts["n:0"]
    [moved] = [c for c in impact.criteria if c.level]
    assert (moved.timing, moved.timing_source) == (ImpactTiming.YEAR, "estimate")


def test_contract_keeps_old_points_assessments_readable() -> None:
    data = assessment({C.REVENUE: Effect(1, "Спрос растёт")}, "—", "llm", "medium").model_dump()
    legacy = {"revenue", "costs", "market_access", "cost_of_money", "regulatory"}
    old = [
        {**c, "points": 20} if c["id"] == "revenue" else c
        for c in data["criteria"]
        if c["id"] in legacy
    ]
    data.pop("scale")
    stored = ImpactAssessment.model_validate({**data, "criteria": old, "score": 20})
    assert (stored.score, stored.scale) == (20, "points")


async def test_news_figures_without_a_date_are_not_a_stated_term() -> None:
    title = "Юань подешевел с 12,85 до 12,19 рубля к 23 октября"
    rate = FakeLLM(answer(judged("n1", fx=(1, "Юань 12,19 вместо 12,85", "now", "source"))))
    impact = (await assess(rate, profile(), priced(title))).impacts["n:0"]
    [moved] = [c for c in impact.criteria if c.level]
    assert moved.timing_source == "estimate"
    day = FakeLLM(answer(judged("n1", fx=(1, "Курс снизился к 23 октября", "now", "source"))))
    impact = (await assess(day, profile(), priced(title))).impacts["n:0"]
    [moved] = [c for c in impact.criteria if c.level]
    assert moved.timing_source == "source"


async def test_score_is_the_sum_of_all_answer_levels() -> None:
    llm = FakeLLM(
        answer(
            judged(
                "n1",
                q14=(-1, "Долг подорожает"),
                q15=(-1, "Лизинг подорожает"),
                q16=(-1, "Новые кредиты дороже"),
                q24=(1, "Импорт дешевле"),
            )
        )
    )
    found = (await assess(llm, profile(), cards(1))).impacts["n:0"]
    moved = {c.id: (c.points, c.level) for c in found.criteria if c.points}
    assert moved == {C.COST_OF_MONEY: (-3, -2), C.FX: (1, 1)}
    assert found.score == -2
    assert [a.question_id for a in found.answers] == [
        "money.debt",
        "money.leasing",
        "money.funding",
        "fx.imports",
    ]
    assert found.answers[0].question.startswith("Как событие скажется через")


async def test_opposite_answers_in_a_group_net_out() -> None:
    balanced = FakeLLM(answer(judged("n1", q7=(1, "Сырьё дешевле"), q9=(-1, "Логистика дороже"))))
    found = (await assess(balanced, profile(), cards(1))).impacts["n:0"]
    assert found.score == 0 and len(found.answers) == 2
    assert found.title == "Двоякое влияние"
    assert (
        "уравновешены" in found.rationale and "стоимость сырья и материалов +1" in found.rationale
    )
    assert all(c.points == 0 and c.reason is None for c in found.criteria)
    tilted = FakeLLM(
        answer(
            judged(
                "n1",
                q7=(1, "Сырьё дешевле"),
                q9=(-1, "Логистика дороже"),
                q8=(-1, "Поставщик поднял цены"),
            )
        )
    )
    found = (await assess(tilted, profile(), cards(1))).impacts["n:0"]
    [costs] = [c for c in found.criteria if c.points]
    assert (costs.points, costs.reason) == (-1, "Логистика дороже")


def test_contract_rejects_criterion_that_ignores_its_answers() -> None:
    from app.contracts.rank import ImpactAnswer

    answers = [
        ImpactAnswer(
            question_id="costs.logistics",
            question="Как событие скажется через логистику и транспорт?",
            criterion=C.COSTS,
            level=-2,
            reason="Тарифы РЖД +12%",
        )
    ]
    good = assessment({C.COSTS: Effect(-2, "Тарифы РЖД +12%")}, "—", "llm", "high", None, answers)
    data = good.model_dump()
    data["criteria"] = [
        {**c, "level": -1, "points": -1} if c["id"] == "costs" else c for c in data["criteria"]
    ]
    data["score"] = -1
    with pytest.raises(ValueError, match="сумме уровней"):
        ImpactAssessment.model_validate(data)


async def test_unknown_question_numbers_are_dropped() -> None:
    llm = FakeLLM(answer(judged("n1", q51=(-2, "лишний"), q8=(-1, "Поставщик поднял цены"))))
    found = (await assess(llm, profile(), cards(1))).impacts["n:0"]
    assert [a.question_id for a in found.answers] == ["costs.suppliers"]


def test_prompt_lists_all_fifty_general_questions() -> None:
    from app.rank.sentiment import QUESTIONS

    text = system_prompt()
    assert len(QUESTIONS) == 50 and len({q for q, _, _ in QUESTIONS}) == 50
    assert all(f"q{index}. " in text for index in range(1, 51))
    assert {criterion for _, criterion, _ in QUESTIONS} == set(C)


async def test_refined_impact_replaces_batch_estimate_in_feed() -> None:
    from app.rank.sentiment import refine

    cache = MemoryCache()
    news, why = cards(1)[0]
    reply = answer(judged("n1", q14=(-1, "Ставка удержит долг дорогим")))
    refined = await refine(
        FakeLLM(reply), profile(), news, why, ["Ставка 14% держит долг дорогим"], cache
    )  # type: ignore[arg-type]
    assert refined is not None and refined.score == -1
    down = FakeLLM(BudgetExceededError("Бюджет исчерпан"))
    feed = await assess(down, profile(), [(news, why)], cache)  # type: ignore[arg-type]
    assert down.calls == [] and feed.impacts[news.id] == refined
