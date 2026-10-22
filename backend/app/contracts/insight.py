from collections.abc import Iterator
from enum import StrEnum
from typing import Literal, Self

from pydantic import AwareDatetime, Field, HttpUrl, model_validator

from app.contracts.common import (
    SCHEMA_VERSION,
    BankProduct,
    Contract,
    Id,
    Score,
    Significance,
    Text,
    require_unique,
)
from app.llm.models import AllowedModel


class SourceType(StrEnum):
    NEWS = "news"
    BANK_ANALYTICS = "bank_analytics"
    PROFILE_FEATURE = "profile_feature"


class Origin(StrEnum):
    FACT = "fact"
    BANK = "bank"
    CLIENT_DATA = "client_data"
    MODEL = "model"


class SourceRef(Contract):
    """Первоисточник, на который ссылаются утверждения вывода."""

    id: Id = Field(description="Локальный идентификатор внутри вывода, например s1")
    type: SourceType
    ref_id: Id = Field(description="NewsItem.id или ProfileFeature.id")
    fragment_id: Id | None = Field(default=None, description="Fragment.id или KeyFact.id")
    title: Text
    url: HttpUrl | None = None
    quote: Text | None = None


class Support(StrEnum):
    SOURCE = "source"
    COMPANY_DATA = "company_data"
    ASSUMPTION = "assumption"


class Basis(Contract):
    """Фрагмент документа или признак компании, из которого получено утверждение."""

    source_id: Id = Field(description="SourceRef.id внутри вывода")
    type: SourceType
    ref_id: Id = Field(description="NewsItem.id или ProfileFeature.id")
    fragment_id: Id | None = Field(default=None, description="Fragment.id или KeyFact.id")
    quote: Text | None = Field(default=None, description="Дословная выдержка из фрагмента")


class Statement(Contract):
    id: Id
    text: Text
    origin: Origin = Field(
        description=(
            "fact — подтверждено источником, bank — позиция Альфа-Банка, "
            "client_data — из данных о компании, model — вывод модели"
        )
    )
    source_ids: list[Id] = Field(min_length=1, description="Ссылки на SourceRef.id")
    confidence: Literal["low", "medium", "high"] | None = None
    basis: list[Basis] = Field(
        default_factory=list,
        description="Заполняет сервер: фрагменты документов и признаки, подтверждающие текст",
    )
    support: Support | None = Field(
        default=None,
        description=(
            "Заполняет сервер: source — подтверждено фрагментом документа, company_data — "
            "данными компании, assumption — подтверждения нет, это предположение модели"
        ),
    )


class Impact(Statement):
    horizon: Literal["short", "medium", "long"] | None = None
    magnitude: Significance | None = None


class ActionKind(StrEnum):
    ACTION = "action"
    QUESTION = "question"
    SCENARIO = "scenario"
    BANK_PRODUCT = "bank_product"


class Action(Statement):
    kind: ActionKind
    product: BankProduct | None = None


class InsightStatus(StrEnum):
    READY = "ready"
    NO_SIGNIFICANT_LINK = "no_significant_link"
    INSUFFICIENT_DATA = "insufficient_data"


class CheckProblem(StrEnum):
    UNSUPPORTED = "unsupported"
    CONTRADICTION = "contradiction"
    NOT_PERSONAL = "not_personal"
    TRIVIAL = "trivial"
    OTHER = "other"


class CheckIssue(Contract):
    statement_id: Id | None = None
    problem: CheckProblem
    detail: Text
    resolved: bool


class SelfCheck(Contract):
    passed: bool
    iterations: int = Field(ge=1)
    issues: list[CheckIssue] = Field(default_factory=list)


ORIGIN_SOURCES = {
    Origin.FACT: {SourceType.NEWS, SourceType.BANK_ANALYTICS},
    Origin.BANK: {SourceType.BANK_ANALYTICS},
    Origin.CLIENT_DATA: {SourceType.PROFILE_FEATURE},
    Origin.MODEL: set(SourceType),
}


class Insight(Contract):
    """Персональная интерпретация события: ответ на шесть вопросов кейса."""

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    id: Id
    company_id: Id
    news_ids: list[Id] = Field(min_length=1)
    run_id: Id = Field(description="Идентификатор разбора для трассировки шагов агента")
    created_at: AwareDatetime
    status: InsightStatus
    status_reason: Text | None = Field(
        default=None, description="Почему вывод не готов; обязательно, если status не ready"
    )
    headline: Text
    significance: Significance
    relevance: Score
    what_happened: list[Statement] = Field(default_factory=list)
    why_it_matters: list[Statement] = Field(default_factory=list)
    risks: list[Impact] = Field(default_factory=list)
    opportunities: list[Impact] = Field(default_factory=list)
    actions: list[Action] = Field(default_factory=list)
    bank_view: list[Statement] = Field(default_factory=list)
    sources: list[SourceRef] = Field(default_factory=list)
    self_check: SelfCheck
    models: list[AllowedModel] = Field(default_factory=list)

    def statements(self) -> Iterator[Statement]:
        yield from self.what_happened
        yield from self.why_it_matters
        yield from self.risks
        yield from self.opportunities
        yield from self.actions
        yield from self.bank_view

    @model_validator(mode="after")
    def traceable(self) -> Self:
        require_unique((source.id for source in self.sources), "источников")
        require_unique((statement.id for statement in self.statements()), "утверждений")
        sources = {source.id: source.type for source in self.sources}
        for statement in self.statements():
            missing = [ref for ref in statement.source_ids if ref not in sources]
            if missing:
                raise ValueError(
                    f"Утверждение {statement.id} ссылается на неизвестные источники: "
                    + ", ".join(missing)
                )
            if any(b.source_id not in statement.source_ids for b in statement.basis):
                raise ValueError(f"Основание утверждения {statement.id} не из его источников")
            cited = {sources[ref] for ref in statement.source_ids}
            if not cited & ORIGIN_SOURCES[statement.origin]:
                raise ValueError(
                    f"Утверждение {statement.id} с происхождением {statement.origin} "
                    "не опирается на подходящий источник"
                )
        cited_material = {
            source.ref_id
            for source in self.sources
            if source.type is not SourceType.PROFILE_FEATURE
        }
        if not set(self.news_ids) <= cited_material:
            raise ValueError("Каждый материал из news_ids должен быть среди источников")
        return self

    @model_validator(mode="after")
    def blocks_match_questions(self) -> Self:
        if any(item.origin not in {Origin.FACT, Origin.MODEL} for item in self.what_happened):
            raise ValueError("В «что произошло» только факты и пересказ модели")
        if any(item.origin is not Origin.BANK for item in self.bank_view):
            raise ValueError("В «как оценивает банк» только позиция банка")
        sources = {source.id: source.type for source in self.sources}
        for item in self.why_it_matters:
            if SourceType.PROFILE_FEATURE not in {sources[ref] for ref in item.source_ids}:
                raise ValueError(f"Утверждение {item.id} объясняет важность без признака компании")
        for action in self.actions:
            if (action.kind is ActionKind.BANK_PRODUCT) != (action.product is not None):
                raise ValueError(
                    f"У действия {action.id} продукт банка задаётся только при kind=bank_product"
                )
        if self.status is InsightStatus.READY:
            if not self.what_happened or not self.why_it_matters:
                raise ValueError("Готовый вывод отвечает на «что произошло» и «почему важно»")
            if not self.risks and not self.opportunities:
                raise ValueError("Готовый вывод называет хотя бы один риск или возможность")
        elif self.status_reason is None:
            raise ValueError("Для неготового вывода нужна причина в status_reason")
        return self
