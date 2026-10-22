"""A5-03: инструменты агента поверх профиля, поиска, связей и сохранённых оригиналов."""

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import asyncpg
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, HttpUrl, JsonValue

from app.agent.orchestrator import AnalysisContext
from app.agent.tools import Handler, Observation, ToolRegistry
from app.contracts.common import Contract, Id, Text
from app.contracts.links import LinkedCompanies
from app.contracts.news import Marker, MaterialKind, NewsItem
from app.contracts.profile import CompanyProfile
from app.contracts.search import SearchResult
from app.profile.exposure import PaymentExposure, payment_exposure
from app.profile.links import linked_companies
from app.search.service import search


class EmptyArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchArguments(EmptyArguments):
    query: str = Field(min_length=1, max_length=500, pattern=r"\S")
    limit: int = Field(default=5, ge=1, le=10)


class NewsSearchArguments(SearchArguments):
    days: int = Field(default=14, ge=1, le=14)
    markers: list[Marker] = Field(default_factory=list, max_length=10)


class ExposureArguments(EmptyArguments):
    direction: Literal["incoming", "outgoing"]
    partner_ids: list[Id] = Field(default_factory=list, max_length=100)
    segments: list[Id] = Field(default_factory=list, max_length=20)


class SourceArguments(EmptyArguments):
    doc_id: Id
    fragment_id: Id | None = None
    max_chars: int = Field(default=4000, ge=100, le=8000)


class SourceExcerpt(Contract):
    doc_id: Id
    status: Literal["found", "not_found", "unavailable"]
    kind: MaterialKind | None = None
    title: Text | None = None
    source: Text | None = None
    url: HttpUrl | None = None
    published_at: AwareDatetime | None = None
    fragment_id: Id | None = None
    locator: Text | None = None
    text: str | None = Field(default=None, max_length=8000)
    truncated: bool = False
    warnings: list[Text] = Field(default_factory=list)


class CounterpartyGraph(Contract):
    company_id: Id
    status: Literal["available", "unavailable"]
    graph: LinkedCompanies | None = None
    warnings: list[Text] = Field(default_factory=list)


ORIGINALS = (
    "SELECT item FROM news_items WHERE news_id = $1",
    "SELECT item FROM analytics_documents WHERE doc_id = $1",
)


async def original(
    connection: Any, context: AnalysisContext, args: SourceArguments
) -> SourceExcerpt:
    material = next((n for n in context.news if n.id == args.doc_id), None)
    warnings = []
    if material is None:
        for sql in ORIGINALS:
            try:
                record = await connection.fetchrow(sql, args.doc_id)
            except asyncpg.UndefinedTableError:
                warnings.append("Одно из хранилищ материалов ещё не загружено")
                continue
            if record is not None:
                material = NewsItem.model_validate_json(record["item"])
                if material.id != args.doc_id:
                    raise ValueError("Идентификатор материала не совпадает с запросом")
                break
    if material is None:
        return SourceExcerpt(
            doc_id=args.doc_id, status="unavailable" if warnings else "not_found", warnings=warnings
        )
    text, locator = material.text, None
    if args.fragment_id:
        fragment = next((f for f in material.fragments if f.id == args.fragment_id), None)
        if fragment is None:
            return SourceExcerpt(
                doc_id=args.doc_id,
                status="not_found",
                warnings=["Запрошенный фрагмент не найден в материале"],
            )
        text, locator = fragment.text, fragment.locator
    return SourceExcerpt(
        doc_id=material.id,
        status="found",
        kind=material.kind,
        title=material.title,
        source=material.source.name,
        url=material.url,
        published_at=material.published_at,
        fragment_id=args.fragment_id,
        locator=locator,
        text=text[: args.max_chars],
        truncated=len(text) > args.max_chars,
        warnings=warnings,
    )


def create_tools(connection: Any) -> ToolRegistry:
    """Реестр для одного разбора. Соединение принадлежит вызывающему async-контексту.

    ID компании берётся только из AnalysisContext; модель не переключает клиента.
    Поиск — BM25/готовый кеш, без новых вложенных вызовов LLM.
    """
    registry = ToolRegistry()

    async def profile(context: AnalysisContext, arguments: dict[str, JsonValue]) -> Observation:
        return Observation(
            data=context.profile.model_dump(mode="json"),
            source_ids=[f.id for f in context.profile.features],
        )

    async def news(context: AnalysisContext, arguments: dict[str, JsonValue]) -> Observation:
        args = NewsSearchArguments.model_validate(arguments)
        now = datetime.now(UTC)
        result = await search(
            connection,
            args.query,
            kinds=[MaterialKind.NEWS],
            limit=args.limit,
            since=now - timedelta(days=args.days),
            markers=args.markers,
            now=now,
        )
        return Observation(
            data=result.model_dump(mode="json"),
            source_ids=list(dict.fromkeys(h.doc_id for h in result.hits)),
        )

    async def bank(context: AnalysisContext, arguments: dict[str, JsonValue]) -> Observation:
        args = SearchArguments.model_validate(arguments)
        result = await search(
            connection, args.query, kinds=[MaterialKind.BANK_ANALYTICS], limit=args.limit
        )
        return Observation(
            data=result.model_dump(mode="json"),
            source_ids=list(dict.fromkeys(h.doc_id for h in result.hits)),
        )

    async def graph(context: AnalysisContext, arguments: dict[str, JsonValue]) -> Observation:
        company_id = context.profile.company_id
        try:
            result = await linked_companies(connection, company_id)
        except asyncpg.UndefinedTableError:
            result = None
        if result is None:
            response = CounterpartyGraph(
                company_id=company_id,
                status="unavailable",
                warnings=["Граф не загружен или компания не найдена; оборот неизвестен"],
            )
        else:
            if result.company_id != company_id:
                raise ValueError("Граф другого клиента")
            response = CounterpartyGraph(company_id=company_id, status="available", graph=result)
        return Observation(
            data=response.model_dump(mode="json"),
            source_ids=[
                f.id
                for f in context.profile.features
                if response.graph is not None
                and f.id
                in {
                    "feature:counterparties_incoming",
                    "feature:counterparties_outgoing",
                    "feature:group",
                }
            ],
        )

    async def exposure(context: AnalysisContext, arguments: dict[str, JsonValue]) -> Observation:
        args = ExposureArguments.model_validate(arguments)
        result = await payment_exposure(
            connection, context.profile.company_id, args.direction, args.partner_ids, args.segments
        )
        feature = f"feature:counterparties_{args.direction}"
        return Observation(
            data=result.model_dump(mode="json"),
            source_ids=[f.id for f in context.profile.features if f.id == feature],
        )

    async def source(context: AnalysisContext, arguments: dict[str, JsonValue]) -> Observation:
        result = await original(connection, context, SourceArguments.model_validate(arguments))
        return Observation(
            data=result.model_dump(mode="json"),
            source_ids=[result.doc_id] if result.status == "found" else [],
        )

    definitions: list[tuple[str, str, Handler, type[BaseModel], type[BaseModel]]] = [
        (
            "get_profile",
            "Профиль текущей компании с признаками и источниками",
            profile,
            EmptyArguments,
            CompanyProfile,
        ),
        (
            "search_news",
            "Поиск новостей с цитатами, маркерами и периодом до 14 дней",
            news,
            NewsSearchArguments,
            SearchResult,
        ),
        (
            "search_bank_analytics",
            "Поиск позиции Альфа-Банка с цитатой и местом в PDF",
            bank,
            SearchArguments,
            SearchResult,
        ),
        (
            "get_counterparty_graph",
            "Карточки контрагентов, роли, группа и доли платежей",
            graph,
            EmptyArguments,
            CounterpartyGraph,
        ),
        (
            "calculate_exposure",
            "Доля платежей направления по контрагентам и/или сегментам",
            exposure,
            ExposureArguments,
            PaymentExposure,
        ),
        (
            "get_original",
            "Сохранённый оригинал или его фрагмент по ID; не открывает URL",
            source,
            SourceArguments,
            SourceExcerpt,
        ),
    ]
    for name, description, handler, args_model, result_model in definitions:
        registry.register(
            name, description, handler, arguments_model=args_model, result_model=result_model
        )
    return registry
