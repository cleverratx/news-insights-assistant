from typing import Annotated, Any

import asyncpg
from fastapi import APIRouter, HTTPException, Path, Response
from pydantic import BaseModel, ConfigDict, Field

from app.api.companies import CompanyId, Connection
from app.api.knowledge import CONFLICT, NOT_FOUND, store
from app.contracts.competitors import Competitor, Competitors, CompetitorSource
from app.profile import competitors, knowledge

router = APIRouter(prefix="/companies", tags=["competitors"])

CompetitorId = Annotated[str, Path(min_length=1, max_length=200, examples=["wikidata:Q899012"])]


class CompetitorCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=300, examples=["ПАО «Акрон»"])


async def company_competitors(connection: Any, company_id: str) -> Competitors:
    try:
        found = await competitors.competitors_for(connection, company_id)
    except asyncpg.UndefinedTableError:
        raise HTTPException(503, "Выгрузка не загружена: выполните python -m app.data") from None
    if found is None:
        raise HTTPException(404, "Компания не найдена")
    return found


async def competitor_of(connection: Any, company_id: str, competitor_id: str) -> Competitor:
    found = competitors.find(await company_competitors(connection, company_id), competitor_id)
    if found is None:
        raise HTTPException(404, "Такого конкурента нет")
    return found


@router.get("/{company_id}/competitors", response_model=Competitors, responses=NOT_FOUND)
async def get_competitors(company_id: CompanyId, connection: Connection) -> Competitors:
    """Конкуренты: названные клиентом, реальные игроки рынка из открытых источников и похожие
    по отрасли, выручке и региону компании из базы банка. У каждого — почему он в списке."""
    return await company_competitors(connection, company_id)


@router.post(
    "/{company_id}/competitors",
    response_model=Competitor,
    status_code=201,
    responses=NOT_FOUND,
)
async def add_competitor(
    company_id: CompanyId, body: CompetitorCreate, connection: Connection
) -> Competitor:
    """Клиент называет конкурента: он попадает в список и в поиск новостей."""
    await company_competitors(connection, company_id)
    base = await knowledge.knowledge_base(connection, company_id)
    fact = await store(connection, competitors.add_fact(base, body.name))
    return competitors.client_competitor(
        (fact.key or "").removeprefix(competitors.KEY_PREFIX), fact
    )


@router.delete(
    "/{company_id}/competitors/{competitor_id}",
    status_code=204,
    responses=NOT_FOUND | CONFLICT,
)
async def remove_competitor(
    company_id: CompanyId, competitor_id: CompetitorId, connection: Connection
) -> Response:
    """Клиент убирает конкурента. Названного им — удаляем, кандидата системы — исключаем:
    он уходит в excluded, и его можно вернуть."""
    found = await competitor_of(connection, company_id, competitor_id)
    base = await knowledge.knowledge_base(connection, company_id)
    if found.source is CompetitorSource.CLIENT:
        fact = await knowledge.latest_version(connection, found.fact_id or "")
        await store(connection, knowledge.delete(fact))
    elif found.fact_id is None:
        await store(connection, competitors.exclude_fact(base, found))
    return Response(status_code=204)


@router.post(
    "/{company_id}/competitors/{competitor_id}/restore",
    response_model=Competitor,
    responses=NOT_FOUND | CONFLICT,
)
async def restore_competitor(
    company_id: CompanyId, competitor_id: CompetitorId, connection: Connection
) -> Competitor:
    """Вернуть кандидата, которого клиент исключил."""
    found = await competitor_of(connection, company_id, competitor_id)
    if found.fact_id is not None and found.source is not CompetitorSource.CLIENT:
        fact = await knowledge.latest_version(connection, found.fact_id)
        await store(connection, knowledge.delete(fact))
    return await competitor_of(connection, company_id, competitor_id)
