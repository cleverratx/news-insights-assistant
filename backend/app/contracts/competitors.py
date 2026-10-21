from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, HttpUrl, model_validator

from app.contracts.common import SCHEMA_VERSION, Contract, Id, Score, Text, require_unique


class CompetitorSource(StrEnum):
    """bank_data — компания из выгрузки, похожая по признакам; open_source — реальный игрок
    рынка из открытого источника (для демо-легенды); client — назвал клиент."""

    BANK_DATA = "bank_data"
    OPEN_SOURCE = "open_source"
    CLIENT = "client"


class Competitor(Contract):
    """Кандидат в конкуренты. Связь с клиентом одна — «работают на одном рынке»."""

    id: Id = Field(description="SYNTHETIC_U, wikidata:<Q-id> или client:<id>")
    source: CompetitorSource
    company_id: Id | None = Field(default=None, description="SYNTHETIC_U компании из выгрузки")
    name: Text | None = Field(default=None, description="Пусто у обезличенной компании выгрузки")
    industry: Text | None = None
    segment: Id | None = Field(default=None, description="Сегмент рынка из справочника ОКВЭД")
    region: Text | None = None
    revenue_rub: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    score: Score | None = Field(
        default=None, description="Сходство с клиентом по отрасли, выручке и региону"
    )
    reasons: list[Text] = Field(min_length=1, description="Почему это кандидат в конкуренты")
    url: HttpUrl | None = Field(default=None, description="Первоисточник для open_source")
    fact_id: Id | None = Field(
        default=None, description="Факт базы знаний: клиент добавил или исключил компанию"
    )

    @model_validator(mode="after")
    def consistent(self) -> Self:
        from_bank = self.source is CompetitorSource.BANK_DATA
        if from_bank != (self.company_id is not None):
            raise ValueError("SYNTHETIC_U есть у кандидатов из выгрузки и только у них")
        if not from_bank and self.name is None:
            raise ValueError("У конкурента из открытых источников или от клиента есть название")
        return self


class Competitors(Contract):
    """Конкуренты клиента: кандидаты из выгрузки, реальные игроки рынка и названные клиентом."""

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    company_id: Id
    competitors: list[Competitor] = Field(default_factory=list)
    excluded: list[Competitor] = Field(
        default_factory=list, description="Кого клиент убрал из списка: можно вернуть"
    )
    warnings: list[Text] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique(self) -> Self:
        require_unique((item.id for item in [*self.competitors, *self.excluded]), "конкурентов")
        return self
