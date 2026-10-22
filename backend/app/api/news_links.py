from contextlib import suppress
from datetime import UTC, datetime
from typing import Annotated

import asyncpg
from fastapi import APIRouter, HTTPException, Path, Query

from app.agent.threads import links_for, threads
from app.api.companies import Connection
from app.contracts.news import MaterialKind, NewsItem
from app.contracts.news_links import NewsLinks, NewsThreads
from app.news.cards import shared_images
from app.search.service import NEWS_DAYS, load

router = APIRouter(prefix="/news", tags=["news"])
NewsId = Annotated[str, Path(min_length=1, max_length=200)]


async def loaded(connection: Connection) -> tuple[list[NewsItem], list[str], set[str], datetime]:
    now = datetime.now(UTC)
    items, warnings = await load(connection, {MaterialKind.NEWS}, now)
    shared: set[str] = set()
    with suppress(asyncpg.UndefinedTableError):
        shared = await shared_images(connection)
    return items, warnings, shared, now


@router.get(
    "/threads",
    response_model=NewsThreads,
    responses={503: {"description": "База недоступна"}},
)
async def news_threads(
    connection: Connection,
    days: Annotated[int, Query(ge=1, le=NEWS_DAYS)] = 7,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> NewsThreads:
    """Сюжеты: связанные по общим участникам и теме новости с пометками «Продолжение»,
    «Усиливает», «Противоречит» и объяснением связи. Без вызовов LLM."""
    items, warnings, shared, now = await loaded(connection)
    result = threads(items, now=now, days=days, limit=limit, shared=shared)
    return result.model_copy(update={"warnings": warnings})


@router.get(
    "/{news_id}/links",
    response_model=NewsLinks,
    responses={404: {"description": "Новость не найдена"}, 503: {"description": "База недоступна"}},
)
async def news_links(news_id: NewsId, connection: Connection) -> NewsLinks:
    """Связи одной новости с другими за последние две недели. Без вызовов LLM."""
    items, warnings, shared, _ = await loaded(connection)
    result = links_for(news_id, items, shared=shared)
    if result is None:
        raise HTTPException(404, "Новость не найдена")
    return result.model_copy(update={"warnings": warnings})
