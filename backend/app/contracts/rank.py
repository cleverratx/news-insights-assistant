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


class ScoreBreakdown(Contract):
    lexical: float | None = Field(default=None, ge=0, allow_inf_nan=False, description="Сырой BM25")
    lexical_norm: Score | None = None
    llm: Score | None = Field(default=None, description="Оценка переранжирования моделью")
    recency: Score | None = None
    final: Score


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
