import json
from datetime import UTC, datetime, timedelta

import asyncpg

from app.contracts.news import FactStatus, NewsItem
from app.llm.errors import BudgetExceededError
from app.llm.models import Task
from app.news.dedup import dedup_pending
from app.news.extract import (
    AnswerFact,
    extract,
    extract_pending,
    grounded,
    news_prompt,
    numbers_in,
    rule_facts,
)
from app.news.fetch import RawItem, Source, save_items
from app.news.fetch import ensure_schema as ensure_raw
from app.news.normalize import RawRecord, normalize, normalize_pending
from tests.test_data_loader import database
from tests.test_news_classify import FakeLLM

__all__ = ["database"]

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
CBR = Source(id="cbr", name="Банк России", kind="regulator", url="https://cbr.test/rss")
MEDIA = Source(id="media", name="Ведомости", kind="media", url="https://media.test/rss")
PARAGRAPHS = [
    "Совет директоров Банка России 24 октября сохранил ключевую ставку на уровне 14% годовых.",
    "По словам председателя Эльвиры Набиуллиной, инфляционное давление остаётся высоким.",
    "Аналитики ожидают, что ставка может быть снижена до 13% в декабре.",
    "Профицит счёта текущих операций составил 3 300,5 млн долларов.",
]
ANSWER = {
    "facts": [
        {
            "text": "ЦБ сохранил ключевую ставку на уровне 14%",
            "status": "confirmed",
            "fragment_id": "p1",
            "who": ["Банк России"],
            "when": "24 октября",
            "numbers": ["14%"],
            "source_says": "не нужен",
        },
        {
            "text": "Набиуллина: инфляционное давление высокое",
            "status": "reported",
            "fragment_id": "p2",
            "who": ["Эльвира Набиуллина"],
            "source_says": "Эльвира Набиуллина",
        },
        {
            "text": "Аналитики ждут снижения ставки до 13% в декабре",
            "status": "forecast",
            "fragment_id": "p3",
            "numbers": ["13%"],
            "source_says": "аналитики",
        },
        {
            "text": "Профицит составил 3300,5 млн долларов",
            "status": "confirmed",
            "fragment_id": "p4",
            "numbers": ["3300,5 млн"],
        },
        {
            "text": "Ставка снизится до 12% в январе",
            "status": "forecast",
            "fragment_id": "p3",
            "numbers": ["12%"],
        },
        {"text": "Выдуманный абзац", "status": "confirmed", "fragment_id": "p9"},
    ]
}


def news(source: Source = CBR, key: str = "rate", hours: int = 0) -> NewsItem:
    raw = RawRecord(
        raw_id=f"raw:{key}",
        source_id=source.id,
        url=f"https://{source.id}.test/{key}",
        title="Банк России сохранил ключевую ставку 14%",
        fetched_at=NOW + timedelta(hours=hours),
        feed_text="".join(f"<p>{paragraph}</p>" for paragraph in PARAGRAPHS),
        published_at=NOW + timedelta(hours=hours),
    )
    return normalize(raw, source).item


def test_prompt_numbers_paragraphs() -> None:
    prompt = news_prompt(news())
    assert "[p1] Совет директоров" in prompt
    assert "[p4] Профицит" in prompt
    assert prompt.startswith("<<<НОВОСТЬ") and prompt.endswith("НОВОСТЬ>>>")


def test_numbers_compare_without_separators() -> None:
    assert numbers_in("3 300,5 млн и 14%") == {"33005", "14"}
    fragments = {"p1": PARAGRAPHS[0]}
    ok = AnswerFact(text="Ставка 14%", status="confirmed", fragment_id="p1", numbers=["14%"])
    wrong = AnswerFact(text="Ставка 15%", status="confirmed", fragment_id="p1")
    missing = AnswerFact(text="Что-то", status="confirmed", fragment_id="p7")
    assert grounded(ok, fragments)
    assert not grounded(wrong, fragments)
    assert not grounded(missing, fragments)


async def test_extract_keeps_only_grounded_facts() -> None:
    llm = FakeLLM(json.dumps(ANSWER, ensure_ascii=False))
    outcome = await extract(llm, news())
    assert llm.tasks == [Task.EXTRACT]
    assert (outcome.method, outcome.rejected, len(outcome.facts)) == ("llm", 2, 4)
    first, said, forecast, surplus = outcome.facts
    assert (first.id, first.status, first.when, first.who) == (
        "f1",
        FactStatus.CONFIRMED,
        "24 октября",
        ["Банк России"],
    )
    assert first.source_says is None
    assert (said.status, said.source_says) == (FactStatus.REPORTED, "Эльвира Набиуллина")
    assert (forecast.status, forecast.fragment_id) == (FactStatus.FORECAST, "p3")
    assert surplus.numbers == ["3300,5 млн"]
    item = news().model_copy(update={"key_facts": outcome.facts})
    assert NewsItem.model_validate(item.model_dump()) == item


async def test_extract_repairs_then_falls_back() -> None:
    repaired = await extract(FakeLLM('{"facts": "нет"}', json.dumps(ANSWER)), news())
    assert (repaired.method, repaired.attempts) == ("llm", 2)
    broken = await extract(FakeLLM("без json"), news())
    assert broken.method == "rules" and broken.error
    down = await extract(FakeLLM(BudgetExceededError("Бюджет исчерпан")), news())
    assert down.model_down and down.method == "rules"


def test_rule_facts_are_never_confirmed() -> None:
    facts = rule_facts(news(MEDIA))
    assert len(facts) == 2
    assert facts[0].status is FactStatus.REPORTED
    assert facts[0].source_says == "Ведомости"
    assert facts[0].numbers == ["24", "14"]
    assert all(fact.status is not FactStatus.CONFIRMED for fact in facts)


async def test_extract_pending_once_per_story(database: asyncpg.Connection) -> None:
    await ensure_raw(database)
    raws = [
        RawItem(
            source_id=source.id,
            url=f"https://{source.id}.test/rate",
            title="Банк России сохранил ключевую ставку 14%",
            fetched_at=NOW + timedelta(hours=offset),
            published_at=NOW + timedelta(hours=offset),
            feed_text="".join(f"<p>{paragraph}</p>" for paragraph in PARAGRAPHS),
        )
        for source, offset in ((MEDIA, 0), (CBR, 1))
    ]
    await save_items(database, raws)
    await normalize_pending(database, [CBR, MEDIA], now=NOW)
    await dedup_pending(database, now=NOW + timedelta(hours=2))
    llm = FakeLLM(json.dumps(ANSWER, ensure_ascii=False))
    report = await extract_pending(database, llm, [CBR, MEDIA], now=NOW)
    assert (report.stories, report.facts, report.rejected) == (1, 4, 2)
    card = raws[1].raw_id.replace("raw:", "news:")
    saved = await database.fetchrow("SELECT * FROM news_extraction")
    assert saved["news_id"] == card
    facts = await database.fetchval(
        "SELECT jsonb_array_length(item->'key_facts') FROM news_items WHERE news_id = $1", card
    )
    assert facts == 4
    again = await extract_pending(database, llm, [CBR, MEDIA], now=NOW)
    assert again.stories == 0
    assert len(llm.calls) == 1
