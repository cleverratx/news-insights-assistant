from typing import Annotated, Any

import asyncpg
from fastapi import APIRouter, HTTPException, Path, Response
from pydantic import BaseModel, ConfigDict, Field

from app.api.companies import CompanyId, Connection
from app.api.knowledge import CONFLICT, NOT_FOUND, store
from app.contracts.links import LinkedCompanies, LinkedCompanyCard
from app.contracts.profile import CounterpartyRole
from app.profile import knowledge, links

router = APIRouter(prefix="/companies", tags=["links"])

CardId = Annotated[str, Path(min_length=1, max_length=200, examples=["U_52328"])]


class RoleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: CounterpartyRole


class ManualLinkCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=300, examples=["ООО «Северный терминал»"])
    role: CounterpartyRole
    industry: str | None = Field(
        default=None, max_length=300, examples=["Грузоперевозки по железной дороге"]
    )
    okved_code: str | None = Field(default=None, max_length=20, examples=["49.20"])
    note: str | None = Field(default=None, max_length=2000)


async def company_links(connection: Any, company_id: str) -> LinkedCompanies:
    try:
        cards = await links.linked_companies(connection, company_id)
    except asyncpg.UndefinedTableError:
        raise HTTPException(503, "Выгрузка не загружена: выполните python -m app.data") from None
    if cards is None:
        raise HTTPException(404, "Компания не найдена")
    return cards


async def card_of(connection: Any, company_id: str, card_id: str) -> LinkedCompanyCard:
    try:
        return links.find_card(await company_links(connection, company_id), card_id)
    except links.LinkNotFoundError:
        raise HTTPException(404, "Связанной компании нет") from None


@router.get("/{company_id}/links", response_model=LinkedCompanies, responses=NOT_FOUND)
async def get_links(company_id: CompanyId, connection: Connection) -> LinkedCompanies:
    """Связанные компании: контрагенты по платежам с долей в обороте, группа, общие физлица
    и добавленные клиентом. У каждой — тип связи и откуда он известен."""
    return await company_links(connection, company_id)


@router.put(
    "/{company_id}/links/{card_id}/role",
    response_model=LinkedCompanyCard,
    responses=NOT_FOUND | CONFLICT,
)
async def set_role(
    company_id: CompanyId, card_id: CardId, body: RoleUpdate, connection: Connection
) -> LinkedCompanyCard:
    """Клиент задаёт тип связи: покупатель, поставщик или перевозчик. Для компании из данных
    банка это факт базы знаний с историей правок, он главнее легенды и догадки по платежам."""
    card = await card_of(connection, company_id, card_id)
    if card.company_id is None:
        await links.set_manual_role(connection, company_id, card.id, body.role)
    else:
        base = await knowledge.knowledge_base(connection, company_id)
        await store(connection, links.role_fact(base, card, body.role))
    return await card_of(connection, company_id, card_id)


@router.post(
    "/{company_id}/links",
    response_model=LinkedCompanyCard,
    status_code=201,
    responses=NOT_FOUND,
)
async def add_link(
    company_id: CompanyId, body: ManualLinkCreate, connection: Connection
) -> LinkedCompanyCard:
    """Компания, которой нет в данных банка: клиент называет её, тип связи и отрасль. По отрасли
    отбор новостей найдёт её рынок."""
    await company_links(connection, company_id)
    link = links.manual_link(
        company_id,
        body.name,
        body.role,
        industry=body.industry,
        okved_code=body.okved_code,
        note=body.note,
    )
    await links.add_manual(connection, link)
    return links.manual_card(link)


@router.delete("/{company_id}/links/{card_id}", status_code=204, responses=NOT_FOUND)
async def delete_link(company_id: CompanyId, card_id: CardId, connection: Connection) -> Response:
    """Удаление компании, добавленной клиентом. Связи из данных банка не удаляются."""
    await company_links(connection, company_id)
    try:
        await links.remove_manual(connection, company_id, card_id)
    except links.LinkNotFoundError:
        raise HTTPException(404, "Добавленной клиентом компании с таким id нет") from None
    return Response(status_code=204)
