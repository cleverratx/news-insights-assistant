from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, model_validator

from app.contracts.common import SCHEMA_VERSION, Contract, Id, Score, Text, require_unique
from app.contracts.profile import CounterpartyRole


class LinkOrigin(StrEnum):
    PAYMENTS = "payments"
    GROUP = "group"
    SHARED_PERSON = "shared_person"
    CLIENT = "client"


class RoleSource(StrEnum):
    """Откуда тип связи: клиент главнее легенды, легенда главнее догадки по платежам."""

    CLIENT = "client"
    LEGEND = "legend"
    PAYMENTS = "payments"


class Turnover(Contract):
    """Платежи с компанией в одном направлении и её доля среди всех платежей направления."""

    amount_rub: float = Field(ge=0, allow_inf_nan=False, description="PAY_AMT")
    payments_count: int | None = Field(default=None, ge=0, description="PAY_CNT")
    amount_share: Score = Field(description="Доля в сумме платежей направления")
    count_share: Score | None = Field(
        default=None, description="Доля в числе платежей направления; пусто, если PAY_CNT нет"
    )


class LinkedCompanyCard(Contract):
    """Карточка связанной компании: откуда связь, кем приходится клиенту и какой оборот даёт."""

    id: Id = Field(description="SYNTHETIC_U или manual:<id> у компании, добавленной клиентом")
    company_id: Id | None = Field(default=None, description="SYNTHETIC_U; пусто у добавленной")
    name: Text | None = Field(default=None, description="Название из легенды или от клиента")
    name_source: Literal["legend", "client"] | None = None
    origins: list[LinkOrigin] = Field(min_length=1, description="Через что компания связана")
    role: CounterpartyRole | None = None
    role_source: RoleSource | None = None
    role_fact_id: Id | None = Field(
        default=None, description="Факт базы знаний с типом связи: его история правок"
    )
    incoming: Turnover | None = Field(default=None, description="Компания платит клиенту")
    outgoing: Turnover | None = Field(default=None, description="Клиент платит компании")
    industry: Text | None = Field(default=None, description="Вид деятельности словами")
    segment: Id | None = Field(default=None, description="Сегмент рынка из справочника ОКВЭД")
    region: Text | None = None
    revenue_rub: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    note: Text | None = Field(default=None, description="Комментарий клиента")

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if (self.role is None) != (self.role_source is None):
            raise ValueError("У типа связи должен быть источник, а у источника — тип")
        manual = LinkOrigin.CLIENT in self.origins
        if manual == (self.company_id is not None):
            raise ValueError("SYNTHETIC_U есть у компаний из данных банка и только у них")
        if (self.incoming or self.outgoing) and LinkOrigin.PAYMENTS not in self.origins:
            raise ValueError("Оборот есть только у связи через платежи")
        return self


class PaymentTotals(Contract):
    amount_rub: float = Field(ge=0, allow_inf_nan=False)
    payments_count: int = Field(ge=0)
    counterparties: int = Field(ge=0)


class LinkedCompanies(Contract):
    """Связанные компании клиента: контрагенты по платежам, группа, общие физлица и добавленные
    клиентом вручную."""

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    company_id: Id
    cards: list[LinkedCompanyCard] = Field(default_factory=list)
    incoming: PaymentTotals = Field(description="Все входящие платежи: от покупателей")
    outgoing: PaymentTotals = Field(description="Все исходящие платежи: поставщикам")
    hidden: int = Field(
        default=0, ge=0, description="Контрагентов по платежам, не попавших в карточки"
    )
    warnings: list[Text] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_cards(self) -> Self:
        require_unique((card.id for card in self.cards), "карточек")
        return self
