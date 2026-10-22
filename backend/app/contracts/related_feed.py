"""A4-12: отдельная выдача событий о конкурентах и контрагентах."""

from enum import StrEnum
from typing import Literal

from pydantic import AwareDatetime, Field

from app.contracts.common import SCHEMA_VERSION, Contract, Id, Text
from app.contracts.links import LinkOrigin
from app.contracts.news import NewsCard
from app.contracts.profile import CounterpartyRole


class RelatedScope(StrEnum):
    ALL = "all"
    COMPETITORS = "competitors"
    COUNTERPARTIES = "counterparties"


class CompanyMention(Contract):
    id: Id
    name: Text
    relationship: Literal["competitor", "counterparty"]
    origins: list[LinkOrigin] = Field(default_factory=list)
    role: CounterpartyRole | None = None
    method: Literal["entity", "text"]
    evidence: Text = Field(description="Название сущности или фрагмент исходного текста")
    news_id: Id = Field(description="Публикация, в которой найдено упоминание")


class RelatedEvent(Contract):
    news_id: Id
    story_id: Id | None = None
    card: NewsCard
    summary: Text | None = None
    companies: list[CompanyMention] = Field(min_length=1)
    publications: list[NewsCard] = Field(min_length=1)


class RelatedFeed(Contract):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    company_id: Id
    generated_at: AwareDatetime
    period_days: int = Field(ge=1, le=14)
    scope: RelatedScope
    items: list[RelatedEvent] = Field(default_factory=list)
    considered: int = Field(ge=0, description="Публикаций за период до отбора")
    matched: int = Field(ge=0, description="Сюжетов до ограничения limit")
    unnamed_companies: int = Field(ge=0, description="Пропущено компаний без публичного имени")
    warnings: list[Text] = Field(default_factory=list)
