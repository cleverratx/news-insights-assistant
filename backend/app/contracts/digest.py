"""A5-17: сводка за период по ленте компании — общей или должности."""

from typing import Literal, Self

from pydantic import AwareDatetime, Field, model_validator

from app.contracts.common import SCHEMA_VERSION, Contract, Id, Text, require_unique
from app.contracts.generation import DataGap, GenerationUsage
from app.contracts.insight import Origin
from app.contracts.news import NewsCard
from app.contracts.news_links import NewsLink


class DigestPoint(Contract):
    id: Id
    text: Text
    origin: Origin = Field(
        description="fact — из материала, bank — аналитика банка, model — вывод ИИ по материалам"
    )
    news_ids: list[Id] = Field(default_factory=list, description="Материалы-основания")
    feature_ids: list[Id] = Field(default_factory=list, description="Признаки компании")


class DigestStep(Contract):
    name: Text
    status: Literal["ok", "cached", "skipped", "failed"]
    detail: Text


class Digest(Contract):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    company_id: Id
    generated_at: AwareDatetime
    period_days: int = Field(ge=1, le=14)
    role: Text | None = Field(default=None, description="Должность; null — общая лента")
    focus: Text | None = Field(default=None, description="Акцент, заданный пользователем")
    method: Literal["llm", "rules"]
    headline: Text
    events: list[DigestPoint] = Field(default_factory=list, description="Что произошло")
    interplay: list[DigestPoint] = Field(
        default_factory=list, description="Как события влияют друг на друга"
    )
    competitors: list[DigestPoint] = Field(
        default_factory=list, description="Конкуренты и контрагенты"
    )
    think_about: list[DigestPoint] = Field(default_factory=list, description="О чём подумать")
    conclusions: list[DigestPoint] = Field(default_factory=list, description="Выводы")
    links: list[NewsLink] = Field(default_factory=list, description="Связи между событиями")
    sources: list[NewsCard] = Field(default_factory=list)
    steps: list[DigestStep] = Field(default_factory=list, description="Как собрана сводка")
    gaps: list[DataGap] = Field(default_factory=list)
    usage: GenerationUsage
    cache_hit: bool = False
    disclaimer: Text

    def points(self) -> list[DigestPoint]:
        return [
            *self.events,
            *self.interplay,
            *self.competitors,
            *self.think_about,
            *self.conclusions,
        ]

    @model_validator(mode="after")
    def traceable(self) -> Self:
        require_unique((point.id for point in self.points()), "пунктов сводки")
        known = {card.news_id for card in self.sources}
        if any(set(point.news_ids) - known for point in self.points()):
            raise ValueError("Пункт сводки ссылается на материал вне источников")
        if any(not point.news_ids for point in [*self.events, *self.competitors]):
            raise ValueError("События и конкуренты всегда ссылаются на материал")
        return self
