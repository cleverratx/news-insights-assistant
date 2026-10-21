import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import asyncpg

from app.analytics.ingest import ensure_schema as ensure_analytics
from app.contracts.news import (
    Channel,
    Dedup,
    Fragment,
    MaterialKind,
    NewsItem,
    Source,
)
from app.contracts.search import SearchResult
from app.llm.errors import BudgetExceededError
from app.llm.models import AllowedModel, LLMResult, Message, PricingInfo, Task, Usage
from app.search.engine import Index, best_sentence, passages, tokens, verbatim
from app.search.rerank import prompt, rerank
from app.search.service import search
from tests.test_data_loader import database

__all__ = ["database"]

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)


def material(
    item_id: str, kind: MaterialKind, title: str, paragraphs: Sequence[str], days: int = 0
) -> NewsItem:
    bank = kind is MaterialKind.BANK_ANALYTICS
    return NewsItem(
        id=item_id,
        kind=kind,
        source=Source(
            name="Центр макроэкономического анализа Альфа-Банка" if bank else "Ведомости",
            channel=Channel.PDF if bank else Channel.RSS,
        ),
        url=None if bank else f"https://media.test/{item_id}",
        title=title,
        published_at=NOW - timedelta(days=days),
        collected_at=NOW,
        text="\n\n".join(paragraphs),
        fragments=[
            Fragment(id=f"s1-{index}", text=text, locator=f"стр. 1, абзац {index}")
            for index, text in enumerate(paragraphs, start=1)
        ],
        dedup=Dedup(content_hash=item_id),
    )


RATE = material(
    "bank:rate",
    MaterialKind.BANK_ANALYTICS,
    "Ключевая ставка ЦБ РФ",
    [
        "Динамика цены ставки ставки ставки ставки на графике по месяцам",
        "ЦБ сохранил ставку на уровне 14%. Мы ожидаем сохранения ключевой ставки до конца года.",
    ],
)
GOLD = material(
    "bank:gold",
    MaterialKind.BANK_ANALYTICS,
    "Обзор рынка золота",
    ["Цены на золото выросли на 4% за август. Спрос со стороны центробанков остаётся высоким."],
)
NEWS = material(
    "news:coal",
    MaterialKind.NEWS,
    "Экспорт угля вырос",
    ["Экспорт угля в Китай вырос на 12%. Ставка РЖД на перевозку угля не изменилась."],
    days=3,
)
OLD = material(
    "news:old", MaterialKind.NEWS, "Ставка ЦБ год назад", ["ЦБ сохранил ставку."], days=40
)
INDEX = Index(passages([RATE, GOLD, NEWS]))


def test_tokens() -> None:
    assert tokens("Цены на золото выросли на 4,5%") == ["цены", "золот", "вырос", "4,5%"]


def test_bm25_with_query_coverage() -> None:
    found = INDEX.search("ключевая ставка до конца года")
    assert found[0].passage.key == "bank:rate#s1-2"
    gold = INDEX.search("цены на золото")
    assert gold[0].passage.item.id == "bank:gold"
    only_news = INDEX.search("ставка", kinds={MaterialKind.NEWS})
    assert {candidate.passage.item.id for candidate in only_news} == {"news:coal"}
    assert INDEX.search("ставка", since=NOW - timedelta(days=1))[0].passage.item.kind is (
        MaterialKind.BANK_ANALYTICS
    )
    assert INDEX.search("и на в") == []
    assert Index([]).search("ставка") == []


def test_quotes() -> None:
    text = RATE.fragments[1].text
    assert best_sentence(text, tokens("ожидания по ставке до конца года")).startswith("Мы ожидаем")
    assert verbatim("ЦБ  сохранил ставку на уровне 14%.", text)
    assert not verbatim("ЦБ снизил ставку до 13%.", text)
    assert not verbatim("  ", text)


def result(text: str) -> LLMResult:
    return LLMResult(
        text=text,
        model=AllowedModel.GLM,
        usage=Usage(prompt_tokens=1500, completion_tokens=200),
        cost_rub=Decimal("0.2"),
        pricing=PricingInfo(),
    )


class FakeLLM:
    def __init__(self, answer: str | Exception) -> None:
        self.answer = answer
        self.calls: list[tuple[Task, list[Message]]] = []

    async def complete(self, task: Task, messages: Sequence[Message]) -> LLMResult:
        self.calls.append((task, list(messages)))
        if isinstance(self.answer, Exception):
            raise self.answer
        return result(self.answer)


async def test_rerank_reorders_and_checks_quotes() -> None:
    candidates = INDEX.search("ставка")
    keys = [candidate.passage.key for candidate in candidates]
    rate_index = keys.index("bank:rate#s1-2") + 1
    other = next(index for index, key in enumerate(keys, start=1) if index != rate_index)
    answer = {
        "ranking": [
            {"id": f"c{rate_index}", "score": 0.9, "quote": "Выдуманная цитата про ставку."},
            {"id": f"c{other}", "score": 0.1, "quote": ""},
        ]
    }
    llm = FakeLLM(json.dumps(answer, ensure_ascii=False))
    found = await rerank(llm, "ставка", candidates, 5)
    assert found.model == "glm-5.3-flash"
    assert [hit.fragment_id for hit in found.hits] == ["s1-2"]
    top = found.hits[0]
    assert top.rerank == 0.9
    assert top.quote in RATE.fragments[1].text
    assert top.document == "Ключевая ставка ЦБ РФ (21.10.2026), стр. 1, абзац 2"
    task, messages = llm.calls[0]
    assert task is Task.RERANK
    assert "<<<ФРАГМЕНТЫ" in messages[1].content and "игнорируй" in messages[0].content


async def test_rerank_falls_back_to_bm25() -> None:
    candidates = INDEX.search("ставка")
    down = await rerank(FakeLLM(BudgetExceededError("Бюджет исчерпан")), "ставка", candidates, 2)
    assert down.model is None and down.error == "Бюджет исчерпан"
    assert len(down.hits) == 2
    assert down.hits[0].score == 1.0
    broken = await rerank(FakeLLM("не json"), "ставка", candidates, 2)
    assert broken.error and len(broken.hits) == 2
    offline = await rerank(None, "ставка", candidates, 1)
    assert offline.error is None and len(offline.hits) == 1
    assert prompt("ставка", candidates[:1]).startswith("Запрос: ставка")


async def test_search_over_database(database: asyncpg.Connection) -> None:
    missing = await search(database, "ставка", now=NOW)
    assert missing.hits == []
    assert any("Аналитика банка не загружена" in text for text in missing.warnings)
    assert any("Новости не собраны" in text for text in missing.warnings)

    await ensure_analytics(database)
    for item in (RATE, GOLD):
        await database.execute(
            """INSERT INTO analytics_documents (doc_id, sha256, file_name, title, published_at,
               pages, text_source, item, ingested_at)
               VALUES ($1, $1, 'x.pdf', $2, $3, 1, 'ocr', $4::jsonb, $3)""",
            item.id,
            item.title,
            item.published_at,
            item.model_dump_json(),
        )
    await database.execute(
        "CREATE TABLE news_items (news_id text, published_at timestamptz, item jsonb)"
    )
    for item in (NEWS, OLD):
        await database.execute(
            "INSERT INTO news_items VALUES ($1, $2, $3::jsonb)",
            item.id,
            item.published_at,
            item.model_dump_json(),
        )
    await database.execute(
        "INSERT INTO news_items VALUES ('news:bad', $1, '{\"id\": \"broken\"}'::jsonb)", NOW
    )
    found = await search(database, "ставка", now=NOW, limit=5)
    assert isinstance(found, SearchResult)
    assert not found.reranked
    ids = {hit.doc_id for hit in found.hits}
    assert {"bank:rate", "news:coal"} <= ids
    assert "news:old" not in ids
    assert found.warnings == ["Пропущено материалов не по схеме NewsItem: 1"]
    news_hit = next(hit for hit in found.hits if hit.doc_id == "news:coal")
    assert str(news_hit.url) == "https://media.test/news:coal"
    assert news_hit.document.startswith("Ведомости: Экспорт угля вырос (18.10.2026)")
    bank_only = await search(
        database, "ставка", kinds=[MaterialKind.BANK_ANALYTICS], now=NOW, limit=5
    )
    assert {hit.kind for hit in bank_only.hits} == {MaterialKind.BANK_ANALYTICS}
    nothing = await search(database, "космос марсоход", now=NOW)
    assert nothing.warnings == [
        "Пропущено материалов не по схеме NewsItem: 1",
        "По запросу ничего не найдено",
    ]
