from collections.abc import AsyncIterator
from typing import Annotated, Any

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Path

from app.contracts.profile import CompanyProfile
from app.core.db import get_pool
from app.profile.builder import load_profile
from app.profile.knowledge import apply_knowledge, knowledge_base

router = APIRouter(prefix="/companies", tags=["companies"])


async def get_connection() -> AsyncIterator[Any]:
    try:
        pool = await get_pool()
    except (OSError, asyncpg.PostgresError) as error:
        raise HTTPException(503, "База данных недоступна") from error
    async with pool.acquire() as connection:
        yield connection


CompanyId = Annotated[str, Path(min_length=1, max_length=200, examples=["U_30404"])]
Connection = Annotated[Any, Depends(get_connection)]


@router.get(
    "/{company_id}/profile",
    response_model=CompanyProfile,
    responses={
        404: {"description": "Компании нет в загруженной выгрузке"},
        503: {"description": "База недоступна или выгрузка ещё не загружена"},
    },
)
async def company_profile(company_id: CompanyId, connection: Connection) -> CompanyProfile:
    """Бизнес-контекст компании. У каждого признака — поля выгрузки, из которых он получен."""
    try:
        profile = await load_profile(connection, company_id)
    except asyncpg.UndefinedTableError:
        raise HTTPException(503, "Выгрузка не загружена: выполните python -m app.data") from None
    if profile is None:
        raise HTTPException(404, "Компания не найдена")
    try:
        base = await knowledge_base(connection, company_id)
    except asyncpg.UndefinedTableError:
        return profile
    return apply_knowledge(profile, base)
