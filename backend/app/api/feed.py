"""Лента компании (A4-04): материалы за период склеиваются в сюжеты, проходят правила и балл
(A4-01, A4-02), верхних кандидатов проверяет модель (A4-03). В ответе — карточка, тип события,
балл с расшифровкой и объяснение, почему новость для этой компании.

Если модель подтвердила кандидатов, в ленте только подтверждённые: показ нерелевантной
новости — критичная ошибка. Непроверенные можно запросить явно (`unverified=true`). Если
модели нет или бюджет исчерпан, лента строится по правилам и помечена `verified=false`.
"""

import logging
from collections import defaultdict
from collections.abc import AsyncIterator, Sequence
from contextlib import AsyncExitStack, suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated

import asyncpg
from fastapi import APIRouter, Depends, Query

from app.api.companies import CompanyId, Connection, company_profile
from app.cache.store import LLMCache
from app.contracts.common import Significance
from app.contracts.feed import Feed, FeedEvent, FeedFeature, FeedItem, FeedRejection
from app.contracts.news import Marker, MaterialKind, NewsItem
from app.contracts.profile import CompanyProfile
from app.contracts.rank import Importance, ImportanceLevel, RankedItem, RankStage
from app.core.config import get_settings
from app.llm.client import open_llm_client
from app.llm.errors import LLMConfigurationError
from app.news.cards import card, shared_images
from app.news.markers import markers_of, matches, title
from app.rank.diversity import diversify, entry, polarity
from app.rank.importance import DEFAULT_MIN_IMPORTANCE, importance
from app.rank.importance import scale as importance_scale
from app.rank.llm_filter import Completer, verify
from app.rank.score import Scored, score
from app.search.service import NEWS_DAYS, load

DEFAULT_DAYS = 7
DEFAULT_LIMIT = 20
MAX_LIMIT = 50
MAX_REASONS = 2
OTHER_EVENT = "Прочее"
SIGNIFICANCE = {
    ImportanceLevel.CRITICAL: Significance.HIGH,
    ImportanceLevel.IMPORTANT: Significance.HIGH,
    ImportanceLevel.INFO: Significance.MEDIUM,
    ImportanceLevel.BACKGROUND: Significance.LOW,
}

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/companies", tags=["feed"])


async def get_llm() -> AsyncIterator[Completer | None]:
    """Клиент модели на запрос; без ключа — None, и лента строится по правилам."""
    async with AsyncExitStack() as stack:
        llm: Completer | None
        try:
            llm = await stack.enter_async_context(open_llm_client(get_settings()))
        except LLMConfigurationError as error:
            logger.warning("Лента без проверки моделью: %s", error)
            llm = None
        yield llm


LLM = Annotated[Completer | None, Depends(get_llm)]


@dataclass(frozen=True)
class Story:
    news: NewsItem
    scored: Scored
    coverage: int


def stories(profile: CompanyProfile, items: Sequence[NewsItem], now: datetime) -> list[Story]:
    """Одно событие — одна карточка: из публикаций сюжета берём ту, что ближе компании, а число
    изданий идёт в масштаб события."""
    groups: defaultdict[str, list[NewsItem]] = defaultdict(list)
    for item in items:
        groups[item.dedup.cluster_id or item.id].append(item)
    result = []
    for members in groups.values():
        coverage = len({member.source.name for member in members})
        scored = [
            Story(member, score(profile, member, now, coverage), coverage) for member in members
        ]
        result.append(max(scored, key=lambda story: (story.scored.final, story.news.published_at)))
    return result


def explanation(ranked: RankedItem) -> str:
    if ranked.reason:
        return ranked.reason
    reasons = [found.reason for found in ranked.matched_features[:MAX_REASONS]]
    return "; ".join(reasons) or "Совпадений с признаками компании нет"


def feed_item(
    ranked: RankedItem,
    story: Story,
    weight: Importance,
    labels: dict[str, str],
    shared: set[str],
) -> FeedItem:
    news = story.news
    markers = markers_of(news)
    classification = news.classification
    return FeedItem(
        rank=ranked.rank,
        news_id=news.id,
        story_id=news.dedup.cluster_id,
        kind=news.kind,
        card=card(news, shared),
        summary=news.summary,
        coverage=story.coverage,
        event=FeedEvent(
            type=title(markers[0]) if markers else OTHER_EVENT,
            markers=markers,
            topics=classification.topics if classification else [],
            sentiment=classification.sentiment if classification else None,
            horizon=classification.horizon if classification else None,
            polarity=polarity(news),
        ),
        stage=ranked.stage,
        significance=SIGNIFICANCE[weight.level],
        importance=weight,
        score=ranked.score,
        explanation=explanation(ranked),
        features=[
            FeedFeature(
                feature_id=found.feature_id,
                label=labels.get(found.feature_id, found.feature_id),
                reason=found.reason,
                rule=found.rule,
                contribution=found.contribution,
            )
            for found in ranked.matched_features
        ],
    )


async def build_feed(
    profile: CompanyProfile,
    items: Sequence[NewsItem],
    llm: Completer | None,
    *,
    now: datetime,
    days: int = DEFAULT_DAYS,
    limit: int = DEFAULT_LIMIT,
    markers: Sequence[Marker] = (),
    unverified: bool = False,
    min_importance: float = DEFAULT_MIN_IMPORTANCE,
    cache: LLMCache | None = None,
    shared: set[str] | None = None,
) -> Feed:
    since = now - timedelta(days=days)
    fresh = [
        item
        for item in items
        if (item.kind is MaterialKind.BANK_ANALYTICS or item.published_at >= since)
        and matches(item, markers)
    ]
    found = stories(profile, fresh, now)
    by_id = {story.news.id: story for story in found}
    filtered = await verify(llm, profile, [(story.news, story.scored) for story in found], cache)
    verified = filtered.model is not None
    allowed = [
        ranked
        for ranked in filtered.items
        if unverified or not verified or ranked.stage is RankStage.RERANKED
    ]
    weights = {
        ranked.news_id: importance(
            by_id[ranked.news_id].scored, by_id[ranked.news_id].news.kind, ranked.score.llm
        )
        for ranked in allowed
    }
    visible = {
        ranked.news_id: ranked
        for ranked in sorted(allowed, key=lambda ranked: -weights[ranked.news_id].score)
        if weights[ranked.news_id].score >= min_importance
    }
    diverse = diversify(
        [entry(by_id[news_id].news, weights[news_id].score / 10) for news_id in visible]
    )
    shown = [visible[news_id] for news_id in diverse.order][:limit]
    labels = {feature.id: feature.label for feature in profile.features}
    warnings = list(filtered.warnings)
    if filtered.error:
        warnings.append(f"Проверка моделью не удалась: {filtered.error}")

    return Feed(
        company_id=profile.company_id,
        generated_at=now,
        period_days=days,
        items=[
            feed_item(
                ranked.model_copy(update={"rank": rank}),
                by_id[ranked.news_id],
                weights[ranked.news_id],
                labels,
                shared or set(),
            )
            for rank, ranked in enumerate(shown, start=1)
        ],
        verified=verified,
        model=filtered.model,
        considered=len(found),
        dropped_by_rules=sum(1 for rejection in filtered.rejected if rejection.by == "rules"),
        min_importance=min_importance,
        hidden_below_threshold=len(allowed) - len(visible),
        importance_scale=importance_scale(),
        rejected=[
            FeedRejection(
                news_id=rejection.news_id,
                title=by_id[rejection.news_id].news.title,
                reason=rejection.reason,
            )
            for rejection in filtered.rejected
            if rejection.by == "model"
        ],
        adjustments=diverse.notes,
        warnings=warnings,
    )


@router.get(
    "/{company_id}/feed",
    response_model=Feed,
    responses={
        404: {"description": "Компании нет в загруженной выгрузке"},
        503: {"description": "База недоступна или выгрузка ещё не загружена"},
    },
)
async def company_feed(
    company_id: CompanyId,
    connection: Connection,
    llm: LLM,
    days: Annotated[int, Query(ge=1, le=NEWS_DAYS, description="Период в днях")] = DEFAULT_DAYS,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    markers: Annotated[list[Marker] | None, Query(description="Фильтр по маркерам")] = None,
    unverified: Annotated[
        bool, Query(description="Показать и кандидатов, которых модель не проверила")
    ] = False,
    min_importance: Annotated[
        float | None,
        Query(ge=0, le=10, description="Порог важности 0–10; по умолчанию FEED_MIN_IMPORTANCE"),
    ] = None,
) -> Feed:
    """Лента инсайтов компании: новости и аналитика банка, отобранные под её признаки и
    отранжированные по важности 0–10. У каждой — тип события, уровень важности с вкладом
    каждого фактора, балл с расшифровкой признаков и объяснение, почему она важна компании.
    Материалы ниже порога важности скрыты, их число — в hidden_below_threshold."""
    profile = await company_profile(company_id, connection)
    now = datetime.now(UTC)
    items, warnings = await load(connection, {MaterialKind.BANK_ANALYTICS, MaterialKind.NEWS}, now)
    shared: set[str] = set()
    with suppress(asyncpg.UndefinedTableError):
        shared = await shared_images(connection)
    feed = await build_feed(
        profile,
        items,
        llm,
        now=now,
        days=days,
        limit=limit,
        markers=markers or [],
        unverified=unverified,
        min_importance=(
            get_settings().feed_min_importance if min_importance is None else min_importance
        ),
        cache=LLMCache(connection),
        shared=shared,
    )
    return feed.model_copy(update={"warnings": [*warnings, *feed.warnings]})
