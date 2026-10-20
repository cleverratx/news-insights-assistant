from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, model_validator

from app.contracts.common import (
    SCHEMA_VERSION,
    BankProduct,
    Contract,
    Id,
    Score,
    Text,
    require_unique,
)
from app.contracts.news import Topic


class ClientType(StrEnum):
    CLIENT = "client"
    CLOSE_CLIENT = "close_client"
    PROSPECT = "prospect"


class FeatureKind(StrEnum):
    INDUSTRY = "industry"
    SCALE = "scale"
    FINANCE = "finance"
    PRODUCT = "product"
    REGION = "region"
    FOREIGN_TRADE = "foreign_trade"
    GROUP = "group"
    COUNTERPARTY = "counterparty"
    OWNERSHIP = "ownership"
    LIFECYCLE = "lifecycle"


class ProfileFeature(Contract):
    """Признак компании, на который может сослаться вывод."""

    id: Id = Field(description="Идентификатор признака внутри профиля, например feature:loan")
    kind: FeatureKind
    label: Text = Field(description="Формулировка для человека: «есть кредитные продукты»")
    value: str | float | bool | None = None
    source_fields: list[Text] = Field(
        min_length=1, description="Поля выгрузки, из которых получен признак"
    )
    derived: bool = Field(
        default=False, description="Признак вычислен (ранг, доля), а не взят из поля как есть"
    )


class ValueChain(StrEnum):
    EXTRACTION = "extraction"
    MANUFACTURING = "manufacturing"
    UTILITIES = "utilities"
    CONSTRUCTION = "construction"
    WHOLESALE = "wholesale"
    RETAIL = "retail"
    SERVICES = "services"


class IndustryResolution(StrEnum):
    CODE = "code"
    NAME = "name"
    UNKNOWN = "unknown"


class Industry(Contract):
    okved_code: Text | None = None
    okved_name: Text | None = None
    section: Text | None = Field(default=None, description="Раздел ОКВЭД, буква от A до U")
    segment: Id = Field(default="other", description="Укрупнённый сегмент рынка из справочника")
    segment_name: Text | None = None
    value_chain: ValueChain | None = Field(
        default=None, description="Место в цепочке: добыча, производство, опт, розница, услуги"
    )
    resolved_by: IndustryResolution = Field(
        default=IndustryResolution.UNKNOWN,
        description="code — по коду ОКВЭД, name — код битый и отрасль угадана по названию",
    )
    topics: list[Topic] = Field(
        default_factory=list, description="Темы новостей, к которым чувствителен сегмент"
    )
    keywords: list[Text] = Field(
        default_factory=list, description="Отраслевые термины для расширения поискового запроса"
    )


class Scale(Contract):
    revenue_rub: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    employees_band: Text | None = None
    age_band: Text | None = None
    lifecycle_stage: Text | None = Field(default=None, description="ECO_MARK_LABEL")
    is_large_business: bool = Field(description="Выручка от 15 млрд рублей — порог из кейса")


class Location(Contract):
    city: Text | None = None
    region: Text | None = None


class MarketPosition(Contract):
    """Оценка положения среди компаний той же отрасли в выгрузке, а не доля рынка страны."""

    peer_basis: Literal["okved_code", "okved_group"]
    peer_count: int = Field(ge=1)
    revenue_rank: int = Field(ge=1)
    revenue_percentile: Score
    peer_revenue_share: Score

    @model_validator(mode="after")
    def rank_within_peers(self) -> Self:
        if self.revenue_rank > self.peer_count:
            raise ValueError("Место по выручке больше числа компаний отрасли")
        return self


class ForeignTrade(Contract):
    has_customs_records: bool
    countries: list[Text] = Field(default_factory=list)


class CompanyGroup(Contract):
    group_id: Id
    companies_count: int = Field(ge=1)
    industries: list[Text] = Field(default_factory=list)


class Counterparty(Contract):
    company_id: Id
    direction: Literal["incoming", "outgoing"] = Field(
        description="incoming — компания получает деньги от контрагента, outgoing — платит ему"
    )
    amount_rub: float = Field(ge=0, allow_inf_nan=False)
    payments_count: int = Field(ge=0)
    share: Score | None = Field(default=None, description="Доля в платежах этого направления")
    okved_name: Text | None = None
    region: Text | None = None


class DataQuality(Contract):
    missing_fields: list[Text] = Field(default_factory=list)
    warnings: list[Text] = Field(default_factory=list)


class CompanyProfile(Contract):
    """Бизнес-контекст компании, собранный по выгрузкам кейса."""

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    company_id: Id = Field(description="SYNTHETIC_U")
    client_type: ClientType
    ownership: Text | None = None
    industry: Industry
    scale: Scale
    location: Location
    products: list[BankProduct] = Field(default_factory=list)
    foreign_trade: ForeignTrade
    group: CompanyGroup | None = None
    counterparties: list[Counterparty] = Field(default_factory=list)
    linked_persons_count: int = Field(default=0, ge=0)
    has_key_owner_mark: bool | None = Field(default=None, description="NEGATIVEMARK")
    market_position: MarketPosition | None = None
    features: list[ProfileFeature] = Field(min_length=1)
    data_quality: DataQuality = Field(default_factory=DataQuality)

    @model_validator(mode="after")
    def unique_features(self) -> Self:
        require_unique((feature.id for feature in self.features), "признаков")
        return self
