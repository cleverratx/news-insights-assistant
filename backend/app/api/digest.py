import logging
from contextlib import suppress
from datetime import UTC, datetime
from typing import Annotated

import asyncpg
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from app.agent.digest import MAX_COMPETITORS, MAX_EVENTS, build_digest
from app.api.companies import CompanyId, Connection, company_profile
from app.api.competitors import company_competitors
from app.api.feed import LLM, build_feed
from app.api.links import company_links
from app.api.positions import personalize
from app.cache.store import LLMCache
from app.contracts.digest import Digest
from app.contracts.news import MaterialKind
from app.contracts.related_feed import RelatedFeed
from app.core.config import get_settings
from app.news.cards import shared_images
from app.rank.related import build_related_feed
from app.rank.roles import Role, weights_for
from app.search.service import NEWS_DAYS, load

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/companies", tags=["digest"])


class DigestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    days: int = Field(default=7, ge=1, le=NEWS_DAYS)
    role: Role | None = None
    focus: str | None = Field(default=None, min_length=1, max_length=600, pattern=r"\S")
    refresh: bool = Field(default=False, description="Собрать заново, не беря синтез из кеша")


@router.post(
    "/{company_id}/digest",
    response_model=Digest,
    responses={
        404: {"description": "Компании нет в загруженной выгрузке"},
        503: {"description": "База недоступна или выгрузка ещё не загружена"},
    },
)
async def company_digest(
    company_id: CompanyId,
    body: DigestRequest,
    connection: Connection,
    llm: LLM,
    position: Annotated[str | None, Query(max_length=100)] = None,
) -> Digest:
    """Сводка за период по ленте компании — общей или должности: что произошло, как события
    влияют друг на друга, конкуренты, о чём подумать, выводы. Акцент задаётся текстом.
    Может расходовать бюджет LLM; принятый синтез кешируется."""
    profile = await company_profile(company_id, connection)
    profile = await personalize(profile, connection, position, body.role)
    now = datetime.now(UTC)
    items, warnings = await load(connection, {MaterialKind.BANK_ANALYTICS, MaterialKind.NEWS}, now)
    shared: set[str] = set()
    with suppress(asyncpg.UndefinedTableError):
        shared = await shared_images(connection)
    cache = LLMCache(connection)
    feed = await build_feed(
        profile,
        items,
        llm,
        now=now,
        days=body.days,
        limit=MAX_EVENTS,
        min_importance=get_settings().feed_min_importance,
        cache=cache,
        shared=shared,
        role=weights_for(body.role) if body.role is not None else None,
    )
    related: RelatedFeed | None = None
    try:
        related = build_related_feed(
            company_id,
            items,
            await company_competitors(connection, company_id),
            await company_links(connection, company_id),
            now=now,
            days=body.days,
            limit=MAX_COMPETITORS,
            shared=shared,
        )
    except HTTPException as error:
        if error.status_code == 404:
            raise
        logger.warning("Сводка без конкурентов: %s", error.detail)
    digest = await build_digest(
        profile,
        items,
        feed,
        related,
        llm,
        now=now,
        days=body.days,
        role=body.role,
        focus=body.focus,
        cache=cache,
        refresh=body.refresh,
    )
    return digest
