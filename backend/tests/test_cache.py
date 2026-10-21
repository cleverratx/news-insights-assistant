import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import asyncpg
import pytest

from app.cache.store import LLMCache, digest, version_of
from app.llm.errors import BudgetExceededError
from app.news import classify as classify_module
from app.news.classify import classify, classify_pending
from app.news.extract import extract
from app.news.fetch import RawItem, save_items
from app.news.fetch import ensure_schema as ensure_raw
from app.news.normalize import normalize_pending
from app.search.engine import Index, passages
from app.search.rerank import rerank
from tests.test_data_loader import database
from tests.test_news_classify import GOOD, SOURCE, FakeLLM, news
from tests.test_news_extract import ANSWER
from tests.test_news_extract import news as rate_news
from tests.test_search import GOLD, NEWS, RATE

__all__ = ["database"]

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)


def test_versions_follow_prompt() -> None:
    assert version_of("промпт", "схема") == version_of("промпт", "схема")
    assert version_of("промпт", "схема") != version_of("промпт v2", "схема")
    assert len(version_of("x")) == 12
    assert digest("a") != digest("b")


async def test_store_counts_hits_and_keeps_versions(database: asyncpg.Connection) -> None:
    cache = LLMCache(database)
    assert await cache.get("classify", "v1", "текст") is None
    await cache.put("classify", "v1", "текст", {"a": 1}, "glm", 0.1, NOW)
    hit = await cache.get("classify", "v1", "текст", NOW + timedelta(hours=1))
    assert hit is not None and (hit.result, hit.model) == ({"a": 1}, "glm")
    await cache.get("classify", "v1", "текст")
    assert await cache.get("classify", "v2", "текст") is None
    await cache.put("classify", "v2", "текст", {"a": 2}, "glm", 0.1, NOW + timedelta(days=1))
    history = await cache.history("classify", "текст")
    assert [(item.version, item.result, item.hits) for item in history] == [
        ("v1", {"a": 1}, 2),
        ("v2", {"a": 2}, 0),
    ]


async def test_classify_reuses_cached_answer(database: asyncpg.Connection) -> None:
    cache = LLMCache(database)
    llm = FakeLLM(json.dumps(GOOD))
    first = await classify(llm, news(), cache)
    assert not first.cached and len(llm.calls) == 1
    again = await classify(None, news(), cache)
    assert again.cached and again.cost_rub == 0
    assert again.classification == first.classification
    assert len(llm.calls) == 1


async def test_prompt_change_invalidates(
    database: asyncpg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = LLMCache(database)
    await classify(FakeLLM(json.dumps(GOOD)), news(), cache)
    monkeypatch.setattr(classify_module, "SYSTEM_PROMPT", classify_module.SYSTEM_PROMPT + " ")
    miss = await classify(None, news(), cache)
    assert not miss.cached and miss.classification.method == "rules"


async def test_extract_cache_is_grounded_again(database: asyncpg.Connection) -> None:
    cache = LLMCache(database)
    llm = FakeLLM(json.dumps(ANSWER, ensure_ascii=False))
    first = await extract(llm, rate_news(), cache)
    again = await extract(FakeLLM(BudgetExceededError("Бюджет исчерпан")), rate_news(), cache)
    assert again.cached and again.facts == first.facts
    assert again.rejected == first.rejected == 2


async def test_rerank_uses_cache_when_model_is_down(database: asyncpg.Connection) -> None:
    cache = LLMCache(database)
    candidates = Index(passages([RATE, GOLD, NEWS])).search("ставка")
    keys = [candidate.passage.key for candidate in candidates]
    target = keys.index("bank:rate#s1-2") + 1
    answer = {"ranking": [{"id": f"c{target}", "score": 0.9, "quote": ""}]}
    first = await rerank(FakeLLM(json.dumps(answer)), "ставка", candidates, 3, cache)
    down = FakeLLM(BudgetExceededError("Бюджет исчерпан"))
    again = await rerank(down, "ставка", candidates, 3, cache)
    assert again.model == first.model == "deepseek-v4.1-flash"
    assert [hit.fragment_id for hit in again.hits] == ["s1-2"]
    assert down.calls == []


async def test_same_text_under_new_id_is_not_sent_twice(database: asyncpg.Connection) -> None:
    await ensure_raw(database)
    raws = [
        RawItem(
            source_id="media",
            url=f"https://media.test/{key}",
            title="Экспорт угля в Китай вырос на 12%",
            fetched_at=NOW,
            published_at=NOW,
            feed_text="<p>Отгрузки угля в Китай выросли на 12% за сентябрь.</p>",
        )
        for key in ("first", "reprint")
    ]
    await save_items(database, raws)
    await normalize_pending(database, [SOURCE], now=NOW)
    llm = FakeLLM(json.dumps(GOOD))
    report = await classify_pending(database, llm, now=NOW)
    assert (report.classified, report.cached) == (2, 1)
    assert len(llm.calls) == 1
    assert report.cost_rub == Decimal("0.05")
