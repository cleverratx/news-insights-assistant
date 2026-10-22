"""A4-11: веса должности поверх уже найденной связи с компанией."""

from dataclasses import replace
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from app.contracts.news import NewsItem, Topic
from app.contracts.rank import RoleAdjustment
from app.rank.score import Scored

Weight = Annotated[float, Field(ge=0.2, le=2, allow_inf_nan=False)]


class Role(StrEnum):
    CEO = "ceo"
    CFO = "cfo"
    LOGISTICS = "logistics"


class RoleWeights(BaseModel):
    """Настройка роли: неизвестные темы/поля и некорректные веса отклоняются.

    Неуказанный вес равен 1. Пустая настройка сохраняет баллы компании.
    feature_weights адресуют реальные id признаков CompanyProfile.
    """

    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=200)
    topic_weights: dict[Topic, Weight] = Field(default_factory=dict)
    feature_weights: dict[str, Weight] = Field(default_factory=dict)


def weights_for(role: Role) -> RoleWeights:
    if role is Role.CFO:
        return RoleWeights(
            id=role,
            title="Финансовый директор",
            topic_weights={
                Topic.KEY_RATE: 1.6,
                Topic.FX: 1.6,
                Topic.DEBT_MARKET: 1.5,
                Topic.TAXES: 1.4,
                Topic.INFLATION: 1.3,
                Topic.LOGISTICS: 0.4,
            },
        )
    if role is Role.LOGISTICS:
        return RoleWeights(
            id=role,
            title="Руководитель логистики",
            topic_weights={
                Topic.LOGISTICS: 1.6,
                Topic.TRADE: 1.5,
                Topic.SANCTIONS: 1.3,
                Topic.FX: 1.1,
                Topic.KEY_RATE: 0.25,
                Topic.DEBT_MARKET: 0.3,
            },
        )
    return RoleWeights(id=role, title="Руководитель компании")


def apply_role(scored: Scored, news: NewsItem, weights: RoleWeights) -> Scored:
    """Не меняет исходный профиль/балл. Нулевая связь остаётся нулевой.

    Тематический вес — среднее по уникальным темам, включая нейтральные.
    Вес признаков — среднее по их вкладам. Их произведение ограничено 0,2–2.
    Вклады масштабируются вместе с final; свежесть, масштаб и штрафы сохраняются.
    """
    topics = set(news.classification.topics) if news.classification else set()
    topic_weight = (
        sum(weights.topic_weights.get(topic, 1.0) for topic in topics) / len(topics)
        if topics
        else 1.0
    )
    total = sum(found.contribution or 0 for found in scored.contributions)
    feature_weight = (
        sum(
            (found.contribution or 0) * weights.feature_weights.get(found.feature_id, 1.0)
            for found in scored.contributions
        )
        / total
        if total
        else 1.0
    )
    multiplier = max(0.2, min(2.0, topic_weight * feature_weight))
    relevance = round(min(1.0, scored.relevance * multiplier), 4)
    ratio = relevance / scored.relevance if scored.relevance else 1.0
    final = round(scored.final * ratio, 4)
    detail = (
        f"Роль «{weights.title}»: вес тем {topic_weight:.2f}, "
        f"вес признаков {feature_weight:.2f}; множитель связи {multiplier:.2f} "
        f"(предел 1 для связи сохраняется)"
    )
    return replace(
        scored,
        relevance=relevance,
        final=final,
        contributions=[
            found.model_copy(
                update={
                    "contribution": round(
                        (found.contribution or 0)
                        * ratio
                        * weights.feature_weights.get(found.feature_id, 1.0)
                        / feature_weight,
                        4,
                    )
                }
            )
            for found in scored.contributions
        ],
        factors=[*scored.factors, detail],
        role=RoleAdjustment(
            id=weights.id,
            title=weights.title,
            multiplier=round(multiplier, 4),
            company_relevance=scored.relevance,
            role_relevance=relevance,
            detail=detail,
        ),
    )
