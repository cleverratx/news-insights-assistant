import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import asyncpg
import pytest

from app.contracts.news import Horizon, NewsItem, Topic
from app.llm.errors import BudgetExceededError
from app.llm.models import AllowedModel, LLMResult, Message, PricingInfo, Task, Usage
from app.news.classify import (
    MAX_INPUT_CHARS,
    classify,
    classify_pending,
    news_prompt,
    parse_answer,
    rule_classify,
    starts,
    system_prompt,
)
from app.news.dedup import dedup_pending
from app.news.fetch import RawItem, Source, save_items
from app.news.fetch import ensure_schema as ensure_raw
from app.news.normalize import RawRecord, normalize, normalize_pending
from tests.test_data_loader import database

__all__ = ["database"]

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
SOURCE = Source(id="media", name="Ведомости", kind="media", url="https://media.test/rss")
GOOD = {
    "topics": ["commodities", "trade", "commodities"],
    "segments": ["mining_metals", "logistics", "steel_plants"],
    "industries": ["угольная промышленность", " "],
    "regions": ["Кемеровская область"],
    "countries": ["Китай"],
    "entities": ["РЖД"],
    "sentiment": "positive",
    "horizon": "months",
    "confidence": 0.8,
}


def news(
    title: str = "Экспорт угля в Китай вырос", text: str = "Отгрузки угля выросли."
) -> NewsItem:
    raw = RawRecord(
        raw_id="raw:coal",
        source_id="media",
        url="https://media.test/coal",
        title=title,
        fetched_at=NOW,
        feed_text=f"<p>{text}</p>",
        published_at=NOW,
    )
    return normalize(raw, SOURCE).item


def result(text: str) -> LLMResult:
    return LLMResult(
        text=text,
        model=AllowedModel.DEEPSEEK,
        usage=Usage(prompt_tokens=900, completion_tokens=120),
        cost_rub=Decimal("0.05"),
        pricing=PricingInfo(),
    )


class FakeLLM:
    def __init__(self, *answers: str | Exception) -> None:
        self.answers = list(answers)
        self.calls: list[list[Message]] = []
        self.tasks: list[Task] = []

    async def complete(self, task: Task, messages: Sequence[Message]) -> LLMResult:
        self.tasks.append(task)
        self.calls.append(list(messages))
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return result(answer)


def test_prompts_close_lists_and_mark_news_as_data() -> None:
    prompt = system_prompt()
    assert all(topic.value in prompt for topic in Topic)
    assert "- logistics: Транспорт и логистика" in prompt
    assert "- other:" not in prompt.split("Сегменты рынка")[1]
    assert "игнорируй" in prompt
    item = news(text="Текст " * 2000)
    user = news_prompt(item)
    assert user.startswith("<<<НОВОСТЬ\nИсточник: Ведомости\nДата: 2026-10-21")
    assert user.endswith("НОВОСТЬ>>>")
    assert len(user) < MAX_INPUT_CHARS + 300


def test_parse_answer_is_strict_where_it_matters() -> None:
    answer = parse_answer(f"Вот ответ:\n```json\n{json.dumps(GOOD, ensure_ascii=False)}\n```")
    assert answer.topics == [Topic.COMMODITIES, Topic.TRADE]
    assert answer.segments == ["mining_metals", "logistics"]
    assert answer.industries == ["угольная промышленность"]
    assert answer.horizon is Horizon.MONTHS
    with pytest.raises(ValueError, match="нет JSON"):
        parse_answer("не знаю")
    with pytest.raises(ValueError):
        parse_answer(json.dumps(GOOD | {"topics": ["weather"]}))
    with pytest.raises(ValueError):
        parse_answer(json.dumps(GOOD | {"confidence": 3}))


async def test_classify_with_model() -> None:
    llm = FakeLLM(json.dumps(GOOD))
    outcome = await classify(llm, news())
    found = outcome.classification
    assert (found.method, found.model) == ("llm", "deepseek-v4.1-flash")
    assert found.topics == [Topic.COMMODITIES, Topic.TRADE]
    assert found.countries == ["Китай"]
    assert (outcome.attempts, outcome.cost_rub) == (1, Decimal("0.05"))
    assert llm.tasks == [Task.CLASSIFY]
    assert llm.calls[0][1].content.startswith("<<<НОВОСТЬ")


async def test_classify_repairs_once() -> None:
    llm = FakeLLM('{"topics": ["weather"], "confidence": 0.5}', json.dumps(GOOD))
    outcome = await classify(llm, news())
    assert outcome.classification.method == "llm"
    assert outcome.attempts == 2
    assert outcome.cost_rub == Decimal("0.10")
    repair = llm.calls[1]
    assert [message.role for message in repair] == ["system", "user", "assistant", "user"]
    assert repair[-1].content.startswith("Ответ не прошёл проверку")


async def test_classify_falls_back_to_rules() -> None:
    broken = await classify(FakeLLM("нет json"), news())
    assert broken.classification.method == "rules"
    assert broken.attempts == 2 and broken.error
    down = await classify(FakeLLM(BudgetExceededError("Бюджет исчерпан")), news())
    assert down.model_down and down.classification.method == "rules"
    offline = await classify(None, news())
    assert offline.classification.confidence == 0.3


def test_rules() -> None:
    found = rule_classify(news("Экспорт угля через порты Дальнего Востока вырос"))
    assert {Topic.COMMODITIES, Topic.TRADE, Topic.LOGISTICS} <= set(found.topics)
    assert found.method == "rules"
    assert starts("порт", "в портах") and not starts("порт", "импорт")
    assert starts("газ", "цены на газ выросли") and not starts("газ", "газета пишет")
    assert rule_classify(news("Матч закончился вничью", "Счёт 1:1.")).topics == [Topic.OTHER]


async def test_classify_pending_one_call_per_story(database: asyncpg.Connection) -> None:
    await ensure_raw(database)
    titles = [
        "Экспорт угля в Китай вырос на 12% за сентябрь",
        "Экспорт угля в Китай вырос на 12% в сентябре",
        "Бензин на бирже подорожал на 3% за неделю",
    ]
    raws = [
        RawItem(
            source_id="media",
            url=f"https://media.test/{index}",
            title=title,
            fetched_at=NOW + timedelta(minutes=index),
            published_at=NOW + timedelta(minutes=index),
            feed_text=f"<p>{title}. Подробности сделки.</p>",
        )
        for index, title in enumerate(titles)
    ]
    await save_items(database, raws)
    await normalize_pending(database, [SOURCE], now=NOW)
    await dedup_pending(database, now=NOW + timedelta(hours=1))
    llm = FakeLLM(json.dumps(GOOD))
    report = await classify_pending(database, llm, now=NOW)
    assert (report.classified, report.copied, report.by_rules) == (2, 1, 0)
    assert report.cost_rub == Decimal("0.10")
    stored = await database.fetch(
        "SELECT item->'classification'->>'method' AS method FROM news_items ORDER BY news_id"
    )
    assert {row["method"] for row in stored} == {"llm"}
    copied = await database.fetchval(
        "SELECT count(*) FROM news_classification WHERE copied_from IS NOT NULL"
    )
    assert copied == 1
    again = await classify_pending(database, llm, now=NOW)
    assert again.classified == 0


async def test_model_down_stops_calls_and_retry_later(database: asyncpg.Connection) -> None:
    await ensure_raw(database)
    raws = [
        RawItem(
            source_id="media",
            url=f"https://media.test/n{index}",
            title=f"Новость номер {index} про разное {index * 7}",
            fetched_at=NOW,
            published_at=NOW - timedelta(minutes=index),
            feed_text=f"<p>Текст {index}</p>",
        )
        for index in range(3)
    ]
    await save_items(database, raws)
    await normalize_pending(database, [SOURCE], now=NOW)
    down = FakeLLM(BudgetExceededError("Бюджет исчерпан"))
    report = await classify_pending(database, down, now=NOW)
    assert (report.classified, report.by_rules) == (3, 3)
    assert len(down.calls) == 1
    assert report.errors == ["Бюджет исчерпан"]
    back = FakeLLM(json.dumps(GOOD))
    retried = await classify_pending(database, back, retry_rules=True, now=NOW)
    assert (retried.classified, retried.by_rules) == (3, 0)
