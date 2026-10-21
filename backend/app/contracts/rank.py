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


class ScoreBreakdown(Contract):
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
