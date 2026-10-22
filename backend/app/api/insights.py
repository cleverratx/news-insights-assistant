from typing import Annotated, Self

import asyncpg
from fastapi import APIRouter, HTTPException, Path
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.agent.insight import generate_with_trace
from app.agent.tools.builtin import ORIGINALS
from app.agent.trace import ensure_storage, load, save
from app.api.companies import CompanyId, Connection, company_profile
from app.api.feed import LLM
from app.contracts.common import Id, require_unique
from app.contracts.generation import GeneratedInsight
from app.contracts.news import NewsItem
from app.contracts.trace import AgentTrace

router = APIRouter(tags=["insights"])
InsightId = Annotated[str, Path(min_length=1, max_length=200)]


class InsightRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    news_ids: list[Id] = Field(min_length=1, max_length=3)
    focus: str | None = Field(default=None, min_length=1, max_length=600, pattern=r"\S")

    @model_validator(mode="after")
    def unique(self) -> Self:
        require_unique(self.news_ids, "материалов")
        return self


@router.post(
    "/companies/{company_id}/insights",
    response_model=GeneratedInsight,
    responses={
        404: {"description": "Компания или материал не найдены"},
        503: {"description": "Хранилище недоступно или содержит некорректные данные"},
    },
)
async def generate_insight(
    company_id: CompanyId, body: InsightRequest, connection: Connection, llm: LLM
) -> GeneratedInsight:
    """Генерация с критиком и сохранением трассы; может расходовать бюджет LLM."""
    profile = await company_profile(company_id, connection)
    materials = []
    for doc_id in body.news_ids:
        found = None
        available = False
        for sql in ORIGINALS:
            try:
                record = await connection.fetchrow(sql, doc_id)
                available = True
            except asyncpg.UndefinedTableError:
                continue
            if record is not None:
                try:
                    found = NewsItem.model_validate_json(record["item"])
                except ValidationError:
                    raise HTTPException(503, "Материал не соответствует схеме NewsItem") from None
                if found.id != doc_id:
                    raise HTTPException(503, "Идентификатор сохранённого материала не совпадает")
                break
        if found is None:
            raise HTTPException(
                404 if available else 503,
                "Материал не найден" if available else "Материалы не загружены",
            )
        materials.append(found)
    try:
        # Проверяем готовность хранилища до платного вызова LLM.
        await ensure_storage(connection)
    except (OSError, asyncpg.PostgresError):
        raise HTTPException(503, "Хранилище разборов недоступно") from None
    result, trace = await generate_with_trace(profile, materials, connection, llm, focus=body.focus)
    try:
        await save(connection, result, trace)
    except (OSError, asyncpg.PostgresError):
        raise HTTPException(503, "Не удалось сохранить разбор и трассу") from None
    return result


async def saved_analysis(
    connection: Connection, insight_id: str
) -> tuple[GeneratedInsight, AgentTrace]:
    try:
        saved = await load(connection, insight_id)
    except (OSError, asyncpg.PostgresError):
        raise HTTPException(503, "Хранилище разборов недоступно") from None
    except (ValueError, TypeError, KeyError):
        raise HTTPException(503, "Сохранённый разбор не соответствует схеме") from None
    if saved is None:
        raise HTTPException(404, "Разбор не найден")
    return saved


@router.get(
    "/insights/{insight_id}",
    response_model=GeneratedInsight,
    responses={
        404: {"description": "Разбор не найден"},
        503: {"description": "Хранилище недоступно"},
    },
)
async def get_insight(insight_id: InsightId, connection: Connection) -> GeneratedInsight:
    result, _ = await saved_analysis(connection, insight_id)
    return result


@router.get(
    "/insights/{insight_id}/trace",
    response_model=AgentTrace,
    responses={
        404: {"description": "Разбор не найден"},
        503: {"description": "Хранилище недоступно"},
    },
)
async def get_trace(insight_id: InsightId, connection: Connection) -> AgentTrace:
    _, trace = await saved_analysis(connection, insight_id)
    return trace
