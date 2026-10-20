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


class SizeCategory(StrEnum):
    MICRO = "micro"
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"
    LARGEST = "largest"


class Scale(Contract):
    revenue_rub: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    size_category: SizeCategory | None = Field(
        default=None,
        description=(
            "По выручке: до 120 млн — micro, до 800 млн — small, до 2 млрд — medium "
            "(пороги МСП), от 15 млрд — largest (порог кейса), между ними — large"
        ),
    )
    employees_band: Text | None = None
    age_band: Text | None = None
    lifecycle_stage: Text | None = Field(default=None, description="ECO_MARK_LABEL")
    is_large_business: bool | None = Field(
        default=None,
        description="Выручка от 15 млрд рублей — порог из кейса; пусто, если выручки нет",
    )


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


class RelatedCompany(Contract):
    """Компания, связанная с клиентом через группу или общее физлицо."""

    company_id: Id
    relation: Literal["group", "shared_person"]
    okved_name: Text | None = None
    segment: Id | None = Field(default=None, description="Сегмент рынка из справочника ОКВЭД")
    region: Text | None = None
    revenue_rub: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class CompanyGroup(Contract):
    group_id: Id
    companies_count: int = Field(ge=1)
    industries: list[Text] = Field(
        default_factory=list, description="Сегменты рынка компаний группы, от частых к редким"
    )
    members: list[RelatedCompany] = Field(
        default_factory=list, description="Крупнейшие по выручке компании группы, кроме клиента"
    )


class Counterparty(Contract):
    company_id: Id
    direction: Literal["incoming", "outgoing"] = Field(
        description="incoming — компания получает деньги от контрагента, outgoing — платит ему"
    )
    amount_rub: float = Field(ge=0, allow_inf_nan=False)
    payments_count: int | None = Field(default=None, ge=0)
    share: Score | None = Field(default=None, description="Доля в платежах этого направления")
    okved_name: Text | None = None
    segment: Id | None = Field(default=None, description="Сегмент рынка из справочника ОКВЭД")
    region: Text | None = None


class DataQuality(Contract):
    missing_fields: list[Text] = Field(default_factory=list)
    warnings: list[Text] = Field(default_factory=list)


class ExposureDirection(StrEnum):
    RISK = "risk"
    OPPORTUNITY = "opportunity"
    MIXED = "mixed"


class Exposure(Contract):
    """Чем компании грозит или полезна тема новостей и из-за каких её признаков."""

    topic: Topic
    direction: ExposureDirection
    reason: Text
    feature_ids: list[Id] = Field(min_length=1, description="Ссылки на ProfileFeature.id")
    keywords: list[Text] = Field(
        default_factory=list, description="Термины для поиска новостей по этой теме у компании"
    )


class CompanyProfile(Contract):
    """Бизнес-контекст компании, собранный по выгрузкам кейса."""

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    company_id: Id = Field(description="SYNTHETIC_U")
    client_type: ClientType | None = None
    ownership: Text | None = None
    industry: Industry
    scale: Scale
    location: Location
    products: list[BankProduct] = Field(default_factory=list)
    foreign_trade: ForeignTrade
    group: CompanyGroup | None = None
    counterparties: list[Counterparty] = Field(default_factory=list)
    linked_persons_count: int = Field(default=0, ge=0)
    person_regions: list[Text] = Field(
        default_factory=list, description="Регионы связанных физлиц (RC_NAME), без повторов"
    )
    related_companies: list[RelatedCompany] = Field(
        default_factory=list, description="Компании, связанные через общих физлиц"
    )
    has_key_owner_mark: bool | None = Field(default=None, description="NEGATIVEMARK")
    market_position: MarketPosition | None = None
    features: list[ProfileFeature] = Field(min_length=1)
    exposures: list[Exposure] = Field(default_factory=list)
    data_quality: DataQuality = Field(default_factory=DataQuality)

    @model_validator(mode="after")
    def unique_features(self) -> Self:
        require_unique((feature.id for feature in self.features), "признаков")
        known = {feature.id for feature in self.features}
        for exposure in self.exposures:
            missing = [ref for ref in exposure.feature_ids if ref not in known]
            if missing:
                raise ValueError(
                    f"Чувствительность к теме {exposure.topic} ссылается на неизвестные признаки: "
                    + ", ".join(missing)
                )
        return self
