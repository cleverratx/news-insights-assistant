from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, model_validator

from app.contracts.common import (
    SCHEMA_VERSION,
    Contract,
    Id,
    Score,
    Significance,
    Text,
    require_unique,
)


class RankStage(StrEnum):
    CANDIDATE = "candidate"
    RERANKED = "reranked"


class FeatureMatch(Contract):
    feature_id: Id = Field(description="Идентификатор признака из CompanyProfile.features")
    reason: Text
    rule: Id | None = Field(default=None, description="Правило сопоставления, которое сработало")
    weight: Score | None = Field(default=None, description="Вес признака по этому правилу")
    contribution: Score | None = Field(
        default=None, description="Вклад признака в итоговый балл: сумма вкладов равна final"
    )


class RoleAdjustment(Contract):
    id: Id
    title: Text
    multiplier: float = Field(ge=0.2, le=2, allow_inf_nan=False)
    company_relevance: Score
    role_relevance: Score
    detail: Text


class ScoreBreakdown(Contract):
    role: RoleAdjustment | None = None
    lexical: float | None = Field(default=None, ge=0, allow_inf_nan=False, description="Сырой BM25")
    lexical_norm: Score | None = None
    llm: Score | None = Field(default=None, description="Оценка переранжирования моделью")
    relevance: Score | None = Field(
        default=None, description="Совпадение с признаками компании по правилам отбора"
    )
    recency: Score | None = Field(
        default=None, description="Свежесть: 1 — только что, 0,5 — полураспад"
    )
    scale: Score | None = Field(
        default=None, description="Масштаб события: тема, охват изданий, подтверждённые цифры"
    )
    final: Score
    factors: list[Text] = Field(
        default_factory=list,
        description="Пояснения к баллу для интерфейса: свежесть, масштаб, штрафы",
    )


class RankedItem(Contract):
    """Материал ленты, отобранный под конкретную компанию, с объяснением, почему он выше других."""

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    company_id: Id
    news_id: Id
    stage: RankStage
    rank: int = Field(ge=1)
    score: ScoreBreakdown
    significance: Significance
    matched_features: list[FeatureMatch] = Field(default_factory=list)
    matched_terms: list[Text] = Field(default_factory=list)
    reason: Text | None = Field(default=None, description="Короткое объяснение от модели")
    run_id: Id | None = None

    @model_validator(mode="after")
    def reranked_has_llm_score(self) -> Self:
        require_unique((match.feature_id for match in self.matched_features), "признаков")
        if self.stage is RankStage.RERANKED and self.score.llm is None:
            raise ValueError("После переранжирования должна быть оценка модели")
        return self


class ImportanceLevel(StrEnum):
    CRITICAL = "critical"
    IMPORTANT = "important"
    INFO = "info"
    BACKGROUND = "background"


class ImportanceFactor(Contract):
    """Слагаемое балла важности: сколько баллов дал фактор и почему."""

    id: Id = Field(description="relevance, breadth, scale, recency, model, bank")
    title: Text
    points: float = Field(ge=0, le=10, allow_inf_nan=False)
    max_points: float = Field(gt=0, le=10, allow_inf_nan=False)
    detail: Text


class Importance(Contract):
    """Степень важности материала для компании: 0–10, уровень и вклад каждого фактора.
    Сумма points всех факторов равна score."""

    score: float = Field(ge=0, le=10, allow_inf_nan=False)
    level: ImportanceLevel
    title: Text = Field(description="Критично, Важно, К сведению, Фон")
    factors: list[ImportanceFactor] = Field(min_length=1)

    @model_validator(mode="after")
    def factors_add_up(self) -> Self:
        if abs(sum(factor.points for factor in self.factors) - self.score) > 0.051:
            raise ValueError("Сумма вкладов факторов не равна баллу важности")
        return self


class ImportanceScale(Contract):
    """Критерий уровня для пользователя: с какого балла и что это значит."""

    level: ImportanceLevel
    title: Text
    min_score: float = Field(ge=0, le=10, allow_inf_nan=False)
    description: Text


LEVEL_POINTS = {-2: -50, -1: -20, 0: 0, 1: 20, 2: 50}


class ImpactCriterionId(StrEnum):
    REVENUE = "revenue"
    COSTS = "costs"
    MARKET_ACCESS = "market_access"
    COST_OF_MONEY = "cost_of_money"
    REGULATORY = "regulatory"
    LIQUIDITY = "liquidity"
    FX = "fx"
    COUNTERPARTIES = "counterparties"
    COMPETITION = "competition"


class ImpactTiming(StrEnum):
    NOW = "now"
    QUARTER = "quarter"
    YEAR = "year"
    LATER = "later"


TIMING_TITLES = {
    ImpactTiming.NOW: "сразу, в течение месяца",
    ImpactTiming.QUARTER: "в ближайшие 3 месяца",
    ImpactTiming.YEAR: "через 3–12 месяцев",
    ImpactTiming.LATER: "больше чем через год",
}


class ImpactColor(StrEnum):
    RED = "red"
    ORANGE = "orange"
    GRAY = "gray"
    LIGHT_GREEN = "light_green"
    GREEN = "green"


class ImpactCriterion(Contract):
    """Влияние события на один фиксированный критерий: уровень от −2 до +2 и почему.
    Плюс — лучше для компании: для регуляторного риска плюс значит, что риск снижается."""

    id: ImpactCriterionId
    title: Text
    level: int = Field(ge=-2, le=2, description="−2 существенно хуже … +2 существенно лучше")
    points: int = Field(ge=-50, le=50, description="Вклад в балл: ±20 — заметно, ±50 — существенно")
    reason: Text | None = Field(default=None, description="Почему такой уровень; пусто при 0")
    timing: ImpactTiming | None = Field(default=None, description="Когда скажется на компании")
    timing_title: Text | None = Field(default=None, description="Срок словами для карточки")
    timing_source: Literal["source", "estimate"] | None = Field(
        default=None,
        description="source — срок назван в материале, estimate — оценка ИИ",
    )

    @model_validator(mode="after")
    def timed(self) -> Self:
        if (self.timing is None) != (self.timing_source is None):
            raise ValueError("Срок влияния указывается вместе с его основанием")
        if self.timing is not None and self.level == 0:
            raise ValueError("Срок есть только у ненулевого влияния")
        if self.timing_title != (TIMING_TITLES[self.timing] if self.timing else None):
            raise ValueError("Подпись срока не соответствует сроку")
        return self


class ImpactAssessment(Contract):
    """Оценка влияния события на бизнес компании: балл от −100 до +100, цвет и обоснование.
    Балл равен сумме вкладов критериев, ограниченной ±100."""

    score: int = Field(ge=-100, le=100)
    color: ImpactColor
    title: Text = Field(
        description="Сильный риск, Риск, Нейтрально, Возможность, Сильная возможность"
    )
    criteria: list[ImpactCriterion] = Field(
        min_length=5,
        max_length=len(ImpactCriterionId),
        description="Все критерии оценки; в сохранённых до расширения оценках их пять",
    )
    rationale: Text = Field(description="Обоснование балла для карточки")
    method: Literal["llm", "rules"] = Field(
        description="llm — оценила модель с учётом профиля, rules — запасная оценка по теме"
    )
    confidence: Literal["low", "medium", "high"]
    model: Text | None = None

    @model_validator(mode="after")
    def score_from_criteria(self) -> Self:
        require_unique((criterion.id.value for criterion in self.criteria), "критериев")
        total = max(-100, min(100, sum(criterion.points for criterion in self.criteria)))
        if total != self.score:
            raise ValueError("Балл влияния не равен сумме вкладов критериев")
        if any(criterion.points != LEVEL_POINTS[criterion.level] for criterion in self.criteria):
            raise ValueError("Вклад критерия не соответствует уровню")
        return self


class ImpactBand(Contract):
    """Цветовая зона шкалы влияния для легенды в интерфейсе."""

    color: ImpactColor
    title: Text
    min_score: int = Field(ge=-100, le=100)
    max_score: int = Field(ge=-100, le=100)
    hex: Text = Field(pattern=r"^#[0-9A-F]{6}$")


class ImpactCriterionInfo(Contract):
    id: ImpactCriterionId
    title: Text
    description: Text
