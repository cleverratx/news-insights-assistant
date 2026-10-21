"""Поиск как инструмент: собирает материалы из базы, ищет BM25 и переранжирует моделью.
Аналитика банка — из analytics_documents (A3-06), новости — из news_items (A3-02); если
какой-то таблицы ещё нет, ищем по тому, что есть, и пишем об этом в warnings."""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
from pydantic import ValidationError

from app.cache.store import LLMCache
from app.contracts.news import Marker, MaterialKind, NewsItem
from app.contracts.search import SearchResult
from app.news.markers import filter_items
from app.search.engine import Index, passages
from app.search.rerank import Completer, rerank

NEWS_DAYS = 14
SOURCES = {
    MaterialKind.BANK_ANALYTICS: "SELECT item FROM analytics_documents",
    MaterialKind.NEWS: "SELECT item FROM news_items WHERE published_at >= $1",
}
MISSING = {
    MaterialKind.BANK_ANALYTICS: "Аналитика банка не загружена: python -m app.analytics",
    MaterialKind.NEWS: "Новости не собраны: python -m app.news",
}


async def load(
    connection: Any, kinds: set[MaterialKind], now: datetime
) -> tuple[list[NewsItem], list[str]]:
    items: list[NewsItem] = []
    warnings: list[str] = []
    for kind in (MaterialKind.BANK_ANALYTICS, MaterialKind.NEWS):
        if kind not in kinds:
            continue
        args = (now - timedelta(days=NEWS_DAYS),) if kind is MaterialKind.NEWS else ()
        try:
            records = await connection.fetch(SOURCES[kind], *args)
        except asyncpg.UndefinedTableError:
            warnings.append(MISSING[kind])
            continue
        skipped = 0
        for record in records:
            try:
                items.append(NewsItem.model_validate_json(record["item"]))
            except ValidationError:
                skipped += 1
        if skipped:
            warnings.append(f"Пропущено материалов не по схеме NewsItem: {skipped}")
    return items, warnings


async def search(
    connection: Any,
    query: str,
    llm: Completer | None = None,
    *,
    kinds: Sequence[MaterialKind] = (MaterialKind.BANK_ANALYTICS, MaterialKind.NEWS),
    limit: int = 5,
    since: datetime | None = None,
    markers: Sequence[Marker] = (),
    now: datetime | None = None,
) -> SearchResult:
    """Фрагменты по запросу с цитатой и ссылкой. kinds=[bank_analytics] — только позиция
    банка: так агент отвечает на вопрос «как это оценивает банк». markers — только материалы
    с одним из маркеров (A3-10)."""
    wanted = set(kinds)
    items, warnings = await load(connection, wanted, now or datetime.now(UTC))
    index = Index(passages(items))
    only = {item.id for item in filter_items(items, markers)} if markers else None
    candidates = index.search(query, limit=max(limit * 4, 20), kinds=wanted, since=since, only=only)
    reranked = await rerank(llm, query, candidates, limit, LLMCache(connection))
    if reranked.error:
        warnings.append(f"Переранжирование моделью не удалось: {reranked.error}")
    if not candidates:
        warnings.append("По запросу ничего не найдено")
    return SearchResult(
        query=query,
        hits=reranked.hits,
        reranked=reranked.model is not None,
        model=reranked.model,
        searched=len(index.passages),
        warnings=warnings,
    )
