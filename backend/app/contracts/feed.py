from enum import StrEnum
from typing import Literal

from pydantic import AwareDatetime, Field

from app.contracts.common import SCHEMA_VERSION, Contract, Id, Score, Significance, Text
from app.contracts.news import Horizon, Marker, MaterialKind, NewsCard, Topic
from app.contracts.rank import Importance, ImportanceScale, RankStage, ScoreBreakdown


class Polarity(StrEnum):
    RISK = "risk"
    OPPORTUNITY = "opportunity"
    NEUTRAL = "neutral"


class FeedFeature(Contract):
    """Признак компании, из-за которого новость попала в ленту: для «почему это для вас»."""

    feature_id: Id
    label: Text = Field(description="Формулировка признака из профиля")
    reason: Text = Field(description="Как новость связана с этим признаком")
    rule: Id | None = Field(default=None, description="Правило отбора или llm — сослалась модель")
    contribution: Score | None = Field(default=None, description="Вклад признака в итоговый балл")


class FeedEvent(Contract):
    type: Text = Field(description="Тип события для бейджа: название главного маркера")
    markers: list[Marker] = Field(default_factory=list)
    topics: list[Topic] = Field(default_factory=list)
    sentiment: Literal["positive", "negative", "neutral", "mixed"] | None = None
    horizon: Horizon | None = None
    polarity: Polarity | None = Field(
        default=None, description="Риск или возможность по тональности события для бизнеса"
    )


class FeedItem(Contract):
    """Материал ленты компании: карточка, тип события, балл и объяснение релевантности."""

    rank: int = Field(ge=1)
    news_id: Id
    story_id: Id | None = Field(
        default=None, description="Сюжет, если событие пишут несколько изданий"
    )
    kind: MaterialKind = Field(
        description="bank_analytics — позиция Альфа-Банка, news — внешняя новость"
    )
    card: NewsCard
    summary: Text | None = None
    coverage: int = Field(ge=1, description="Сколько изданий пишут о событии")
    event: FeedEvent
    stage: RankStage = Field(description="reranked — релевантность подтвердила модель")
    significance: Significance = Field(description="Из уровня важности: критично и важно — high")
    importance: Importance = Field(description="Балл 0–10, уровень и вклад каждого фактора")
    score: ScoreBreakdown
    explanation: Text = Field(description="Почему эта новость для вас: аргумент модели или правил")
    features: list[FeedFeature] = Field(default_factory=list)


class FeedRejection(Contract):
    news_id: Id
    title: Text
    reason: Text = Field(description="Аргумент модели, почему новость не касается компании")


class Feed(Contract):
    """Отранжированная лента компании. Отклонённые моделью новости видны с аргументом."""

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    company_id: Id
    generated_at: AwareDatetime
    period_days: int = Field(ge=1)
    items: list[FeedItem] = Field(default_factory=list)
    verified: bool = Field(description="Верхних кандидатов проверила модель")
    model: Text | None = None
    considered: int = Field(ge=0, description="Сколько событий рассмотрено за период")
    dropped_by_rules: int = Field(ge=0, description="Отсеяно правилами: не совпали с компанией")
    min_importance: float = Field(ge=0, le=10, description="Порог отсечения по баллу важности")
    hidden_below_threshold: int = Field(ge=0, description="Скрыто: важность ниже порога")
    importance_scale: list[ImportanceScale] = Field(
        default_factory=list, description="Уровни важности и их критерии для пользователя"
    )
    rejected: list[FeedRejection] = Field(
        default_factory=list, description="Отклонено моделью, с аргументом"
    )
    adjustments: list[Text] = Field(
        default_factory=list,
        description="Как изменён порядок ради разнообразия: разведены события, поднят риск",
    )
    warnings: list[Text] = Field(default_factory=list)
