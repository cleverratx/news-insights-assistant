from enum import StrEnum
from typing import Literal, Self

from pydantic import AwareDatetime, Field, model_validator

from app.contracts.common import SCHEMA_VERSION, Contract, Id, Text, require_unique


class FactCategory(StrEnum):
    MARKETS = "markets"
    PRODUCTS = "products"
    SEASONALITY = "seasonality"
    CONSTRAINTS = "constraints"
    SUPPLIERS = "suppliers"
    CUSTOMERS = "customers"
    PLANS = "plans"
    OTHER = "other"


class FactSource(StrEnum):
    """Откуда факт. Клиент главнее данных банка, данные банка главнее вывода системы."""

    CLIENT = "client"
    BANK_DATA = "bank_data"
    SYSTEM = "system"


SOURCE_PRIORITY = {FactSource.CLIENT: 0, FactSource.BANK_DATA: 1, FactSource.SYSTEM: 2}


class FactStatus(StrEnum):
    ASSUMPTION = "assumption"
    CONFIRMED = "confirmed"


class KnowledgeFact(Contract):
    """Одна версия факта о компании. Правка факта — новая версия с тем же id, а не перезапись."""

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    id: Id = Field(description="Идентификатор факта, общий для всех его версий")
    company_id: Id
    version: int = Field(ge=1)
    key: Id | None = Field(
        default=None,
        description=(
            "О чём факт, например markets.export_share. Факты с одним ключом конкурируют: "
            "в базе знаний остаётся один, по приоритету источника"
        ),
    )
    category: FactCategory
    text: Text = Field(description="Формулировка для человека")
    value: str | float | bool | None = None
    source: FactSource
    status: FactStatus = Field(
        description="assumption — выведено из данных и ждёт подтверждения, confirmed — подтверждено"
    )
    evidence: list[Id] = Field(
        default_factory=list,
        description="На чём основан автоматический факт: ProfileFeature.id или поля выгрузки",
    )
    deleted: bool = Field(default=False, description="Версия, которой факт удалён")
    created_at: AwareDatetime = Field(description="Когда создана эта версия")

    @model_validator(mode="after")
    def client_fact_is_confirmed(self) -> Self:
        if self.source is FactSource.CLIENT and self.status is not FactStatus.CONFIRMED:
            raise ValueError("Факт от клиента всегда подтверждён")
        if self.source is not FactSource.CLIENT and not self.evidence and not self.deleted:
            raise ValueError("Автоматический факт должен ссылаться на основание в evidence")
        return self


class KnowledgeBase(Contract):
    """Действующие факты о компании и те, что ими вытеснены."""

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    company_id: Id
    facts: list[KnowledgeFact] = Field(default_factory=list)
    superseded: list[KnowledgeFact] = Field(
        default_factory=list,
        description="Факты с тем же ключом, уступившие по приоритету: видно, что исправил клиент",
    )

    @model_validator(mode="after")
    def consistent(self) -> Self:
        require_unique((fact.id for fact in self.facts), "фактов")
        keys = [fact.key for fact in self.facts if fact.key is not None]
        require_unique(keys, "ключей фактов")
        if any(fact.company_id != self.company_id for fact in [*self.facts, *self.superseded]):
            raise ValueError("В базе знаний факты другой компании")
        if any(fact.deleted for fact in self.facts):
            raise ValueError("Удалённый факт не может быть действующим")
        return self


class Question(Contract):
    """То, чего система о компании не знает и что стоит спросить у клиента."""

    key: Id = Field(description="Ключ факта, под которым ляжет ответ")
    category: FactCategory
    question: Text
    why: Text = Field(description="Зачем это знать: что изменится в отборе и выводах")
    priority: Literal["high", "medium", "low"]
    based_on: list[Id] = Field(
        default_factory=list, description="Признаки профиля, из-за которых вопрос задан именно так"
    )


class KnowledgeGaps(Contract):
    """Чего система не знает о компании и что ей нужно подтвердить."""

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    company_id: Id
    questions: list[Question] = Field(default_factory=list)
    to_confirm: list[KnowledgeFact] = Field(
        default_factory=list, description="Предположения системы и данных банка: верно ли?"
    )
    known: int = Field(ge=0, description="Сколько фактов уже подтвердил клиент")

    @model_validator(mode="after")
    def unique_questions(self) -> Self:
        require_unique((question.key for question in self.questions), "вопросов")
        return self
