import logging
from datetime import timedelta
from typing import Annotated, Self

import asyncpg
from fastapi import APIRouter, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.agent.insight import for_role, generate_with_trace
from app.agent.insight_cache import cache_input, cached, remember
from app.agent.related import focus_for, relation_for, with_relation
from app.agent.tools.builtin import ORIGINALS
from app.agent.trace import ensure_storage, load, save
from app.api.companies import CompanyId, Connection, company_profile
from app.api.competitors import company_competitors
from app.api.feed import LLM
from app.api.links import company_links
from app.api.positions import personalize
from app.contracts.common import Id, require_unique
from app.contracts.generation import GeneratedInsight
from app.contracts.news import NewsItem
from app.contracts.trace import AgentTrace
from app.core.config import get_settings
from app.rank.roles import Role
from app.rank.snapshots import restore

logger = logging.getLogger(__name__)
router = APIRouter(tags=["insights"])
InsightId = Annotated[str, Path(min_length=1, max_length=200)]


class InsightRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    news_ids: list[Id] = Field(min_length=1, max_length=3)
    role: Role | None = None
    feed_snapshot_id: Id | None = None
    focus: str | None = Field(default=None, min_length=1, max_length=600, pattern=r"\S")
    refresh: bool = Field(default=False, description="Пересобрать разбор, не беря его из кеша")
    related_company_id: Id | None = Field(
        default=None,
        description=(
            "Конкурент или контрагент из ленты связей: выжимка события у него и "
            "«что это значит для нас»"
        ),
    )

    @model_validator(mode="after")
    def unique(self) -> Self:
        require_unique(self.news_ids, "материалов")
        return self


@router.post(
    "/companies/{company_id}/insights",
    response_model=GeneratedInsight,
    responses={
        404: {"description": "Компания или материал не найдены"},
        409: {"description": "Снимок ленты не относится к этой компании или материалам"},
        422: {"description": "Связанная компания не найдена или не упоминается в материалах"},
        503: {"description": "Хранилище недоступно или содержит некорректные данные"},
    },
)
async def generate_insight(
    company_id: CompanyId,
    body: InsightRequest,
    connection: Connection,
    llm: LLM,
    position: Annotated[str | None, Query(max_length=100)] = None,
) -> GeneratedInsight:
    """Генерация с критиком и сохранением трассы; может расходовать бюджет LLM. Готовый разбор
    тех же материалов для того же профиля и акцента выдаётся из кеша (`cache_hit`); для другой
    должности — тот же разбор со своими оценками и подачей (`role_view`)."""
    profile = await company_profile(company_id, connection)
    profile = await personalize(profile, connection, position, body.role)
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
    ranking = None
    if body.feed_snapshot_id:
        try:
            ranking = await restore(
                connection, body.feed_snapshot_id, profile, materials, body.role
            )
        except ValueError as error:
            raise HTTPException(409, str(error)) from None
        except (OSError, asyncpg.PostgresError):
            raise HTTPException(503, "Снимок ленты недоступен") from None
    relation = None
    focus = body.focus
    if body.related_company_id:
        relation = relation_for(
            body.related_company_id,
            materials,
            await company_competitors(connection, company_id),
            await company_links(connection, company_id),
        )
        if relation is None:
            raise HTTPException(
                422, "Компании нет среди конкурентов и контрагентов или её нет в материалах"
            )
        profile = with_relation(profile, relation)
        focus = focus_for(relation, body.focus)
    hours = get_settings().llm_insight_cache_hours
    key = cache_input(profile, materials, focus)
    if hours and not body.refresh:
        try:
            hit = await cached(connection, key, timedelta(hours=hours))
        except (OSError, asyncpg.PostgresError, ValueError, TypeError, KeyError) as error:
            logger.warning("Кеш инсайтов недоступен: %s", type(error).__name__)
            hit = None
        if hit is not None:
            saved = hit[0]
            role = body.role.value if body.role else None
            if (saved.role, saved.feed_snapshot_id) == (role, body.feed_snapshot_id):
                return saved
            return await for_role(
                saved,
                profile,
                materials,
                llm,
                connection,
                role=body.role,
                ranking=ranking,
                feed_snapshot_id=body.feed_snapshot_id,
            )
    result, trace = await generate_with_trace(
        profile,
        materials,
        connection,
        llm,
        focus=focus,
        role=body.role,
        ranking=ranking,
        feed_snapshot_id=body.feed_snapshot_id,
        relation=relation,
    )
    try:
        await save(connection, result, trace)
    except (OSError, asyncpg.PostgresError):
        raise HTTPException(503, "Не удалось сохранить разбор и трассу") from None
    if hours:
        try:
            await remember(connection, key, result)
        except (OSError, asyncpg.PostgresError) as error:
            logger.warning("Инсайт не попал в кеш: %s", type(error).__name__)
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
