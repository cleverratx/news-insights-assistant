from contextlib import suppress
from datetime import UTC, datetime
from typing import Annotated

import asyncpg
from fastapi import APIRouter, Query

from app.api.companies import CompanyId, Connection
from app.api.competitors import company_competitors
from app.api.links import company_links
from app.contracts.news import MaterialKind
from app.contracts.related_feed import RelatedFeed, RelatedScope
from app.news.cards import shared_images
from app.rank.related import build_related_feed
from app.search.service import load

router = APIRouter(prefix="/companies", tags=["feed"])


@router.get(
    "/{company_id}/related-feed",
    response_model=RelatedFeed,
    responses={
        404: {"description": "Компания не найдена"},
        503: {"description": "Выгрузка ещё не загружена или база недоступна"},
    },
)
async def related_feed(
    company_id: CompanyId,
    connection: Connection,
    days: Annotated[int, Query(ge=1, le=14)] = 7,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    scope: RelatedScope = RelatedScope.ALL,
) -> RelatedFeed:
    """Новости о конкурентах и контрагентах: совпавшие названия и первоисточники.
    Исключённые конкуренты и удалённые связи не участвуют. Без вызовов LLM.
    """
    competitors = await company_competitors(connection, company_id)
    links = await company_links(connection, company_id)
    now = datetime.now(UTC)
    items, warnings = await load(connection, {MaterialKind.NEWS}, now)
    shared: set[str] = set()
    with suppress(asyncpg.UndefinedTableError):
        shared = await shared_images(connection)
    result = build_related_feed(
        company_id,
        items,
        competitors,
        links,
        now=now,
        days=days,
        limit=limit,
        scope=scope,
        shared=shared,
    )
    return result.model_copy(update={"warnings": [*warnings, *result.warnings]})
