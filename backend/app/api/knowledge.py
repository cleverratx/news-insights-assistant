from typing import Annotated, Any

import asyncpg
from fastapi import APIRouter, HTTPException, Path, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from app.api.companies import CompanyId, Connection
from app.contracts.knowledge import FactCategory, FactSource, KnowledgeBase, KnowledgeFact
from app.profile import knowledge

router = APIRouter(prefix="/companies", tags=["knowledge"])

FactId = Annotated[str, Path(min_length=1, max_length=300)]
Responses = dict[int | str, dict[str, Any]]
NOT_FOUND: Responses = {404: {"description": "Компании или факта нет"}}
CONFLICT: Responses = {409: {"description": "Факт удалён или изменён с момента чтения"}}


class FactCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: FactCategory
    text: str = Field(min_length=1, max_length=2000)
    key: str | None = Field(default=None, max_length=200, examples=["markets.export_share"])
    value: str | float | bool | None = None


class FactUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str | None = Field(default=None, min_length=1, max_length=2000)
    category: FactCategory | None = None
    value: str | float | bool | None = None
    expected_version: int | None = Field(
        default=None, ge=1, description="Версия, которую видел клиент: защита от двойной правки"
    )


async def company_knowledge(connection: Any, company_id: str) -> KnowledgeBase:
    try:
        base = await knowledge.knowledge_for(connection, company_id)
    except asyncpg.UndefinedTableError:
        raise HTTPException(503, "Выгрузка не загружена: выполните python -m app.data") from None
    if base is None:
        raise HTTPException(404, "Компания не найдена")
    return base


async def company_fact(connection: Any, company_id: str, fact_id: str) -> KnowledgeFact:
    await company_knowledge(connection, company_id)
    try:
        fact = await knowledge.latest_version(connection, fact_id)
    except knowledge.FactNotFoundError:
        raise HTTPException(404, "Факт не найден") from None
    if fact.company_id != company_id:
        raise HTTPException(404, "Факт не найден")
    if fact.deleted:
        raise HTTPException(409, "Факт удалён")
    return fact


async def store(connection: Any, fact: KnowledgeFact) -> KnowledgeFact:
    try:
        return await knowledge.save(connection, fact)
    except (ValueError, asyncpg.UniqueViolationError):
        raise HTTPException(409, "Факт изменили параллельно: перечитайте и повторите") from None


@router.get("/{company_id}/knowledge", response_model=KnowledgeBase, responses=NOT_FOUND)
async def get_knowledge(company_id: CompanyId, connection: Connection) -> KnowledgeBase:
    """База знаний: черновик из данных банка и правки клиента. Факт клиента главнее."""
    return await company_knowledge(connection, company_id)


@router.get(
    "/{company_id}/knowledge/history", response_model=list[KnowledgeFact], responses=NOT_FOUND
)
async def get_history(
    company_id: CompanyId,
    connection: Connection,
    fact_id: Annotated[str | None, Query(max_length=300)] = None,
) -> list[KnowledgeFact]:
    """Все версии фактов компании: кто, что и когда менял."""
    await company_knowledge(connection, company_id)
    versions = await knowledge.history(connection, company_id)
    return [fact for fact in versions if fact_id is None or fact.id == fact_id]


@router.post(
    "/{company_id}/knowledge/facts",
    response_model=KnowledgeFact,
    status_code=201,
    responses=NOT_FOUND,
)
async def add_fact(
    company_id: CompanyId, body: FactCreate, connection: Connection
) -> KnowledgeFact:
    """Новый факт от клиента: сразу подтверждён и главнее автоматического с тем же ключом."""
    await company_knowledge(connection, company_id)
    fact = knowledge.new_fact(
        company_id, body.category, body.text, FactSource.CLIENT, key=body.key, value=body.value
    )
    return await store(connection, fact)


@router.patch(
    "/{company_id}/knowledge/facts/{fact_id}",
    response_model=KnowledgeFact,
    responses=NOT_FOUND | CONFLICT,
)
async def edit_fact(
    company_id: CompanyId, fact_id: FactId, body: FactUpdate, connection: Connection
) -> KnowledgeFact:
    """Исправление факта клиентом: новая версия, факт становится подтверждённым фактом клиента."""
    fact = await company_fact(connection, company_id, fact_id)
    if body.expected_version is not None and body.expected_version != fact.version:
        raise HTTPException(409, f"Факт уже изменён: текущая версия {fact.version}")
    changes = body.model_dump(exclude_unset=True, exclude={"expected_version"})
    return await store(connection, knowledge.revise(fact, source=FactSource.CLIENT, **changes))


@router.post(
    "/{company_id}/knowledge/facts/{fact_id}/confirm",
    response_model=KnowledgeFact,
    responses=NOT_FOUND | CONFLICT,
)
async def confirm_fact(
    company_id: CompanyId, fact_id: FactId, connection: Connection
) -> KnowledgeFact:
    """Клиент подтвердил предположение: «подтверждено клиентом», дальше данные его не меняют."""
    fact = await company_fact(connection, company_id, fact_id)
    return await store(connection, knowledge.confirm(fact))


@router.delete(
    "/{company_id}/knowledge/facts/{fact_id}",
    status_code=204,
    responses=NOT_FOUND | CONFLICT,
)
async def delete_fact(company_id: CompanyId, fact_id: FactId, connection: Connection) -> Response:
    """Удаление факта клиентом. История остаётся; черновик этот факт больше не вернёт."""
    fact = await company_fact(connection, company_id, fact_id)
    await store(connection, knowledge.delete(fact))
    return Response(status_code=204)
