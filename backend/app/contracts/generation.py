from decimal import Decimal
from typing import Literal, Self

from pydantic import Field, model_validator

from app.contracts.common import Contract, Id, Text
from app.contracts.insight import (
    Action,
    Impact,
    Insight,
    InsightStatus,
    Origin,
    SourceRef,
    Statement,
)
from app.llm.models import AllowedModel

type BlockId = Literal["event", "relevance", "consequences", "actions", "bank", "basis"]
type ExecutionStatus = Literal[
    "completed", "step_limit", "timeout", "planner_error", "check_failed", "unavailable"
]


class InsightBlock(Contract):
    id: BlockId
    title: Text
    statement_ids: list[Id] = Field(default_factory=list)
    source_ids: list[Id] = Field(default_factory=list)
    empty_reason: Text | None = None


class LabeledStatement(Contract):
    statement: Statement | Impact | Action
    label: Text
    source_ids: list[Id]


class InsightPresentation(Contract):
    facts: list[LabeledStatement] = Field(default_factory=list)
    bank_position: list[LabeledStatement] = Field(default_factory=list)
    ai_conclusions: list[LabeledStatement] = Field(default_factory=list)
    client_context: list[LabeledStatement] = Field(default_factory=list)
    blocks: list[InsightBlock] = Field(min_length=6, max_length=6)
    originals: list[SourceRef] = Field(
        default_factory=list, description="Оригинальные цитаты отдельно от текста интерпретации"
    )


class GenerationUsage(Contract):
    calls: int = Field(default=0, ge=0)
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    known_cost_rub: Decimal = Field(default=Decimal(0), ge=0, allow_inf_nan=False)
    models: list[AllowedModel] = Field(default_factory=list)
    pricing_modes: list[Literal["configured", "public_estimate"]] = Field(default_factory=list)


class GeneratedInsight(Contract):
    insight: Insight
    presentation: InsightPresentation
    usage: GenerationUsage
    execution_status: ExecutionStatus
    warnings: list[Text] = Field(default_factory=list)

    @model_validator(mode="after")
    def separated(self) -> Self:
        if self.insight.status is InsightStatus.READY and (
            not self.insight.self_check.passed or not self.insight.actions
        ):
            raise ValueError("Готовый инсайт должен пройти проверку и содержать действия")
        seen: list[str] = []
        for origin, group in [
            (Origin.FACT, self.presentation.facts),
            (Origin.BANK, self.presentation.bank_position),
            (Origin.MODEL, self.presentation.ai_conclusions),
            (Origin.CLIENT_DATA, self.presentation.client_context),
        ]:
            if any(row.statement.origin is not origin for row in group):
                raise ValueError("Смешано происхождение утверждений")
            seen.extend(row.statement.id for row in group)
        statements = {s.id: s for s in self.insight.statements()}
        for group in (
            self.presentation.facts,
            self.presentation.bank_position,
            self.presentation.ai_conclusions,
            self.presentation.client_context,
        ):
            for row in group:
                original = statements.get(row.statement.id)
                if original != row.statement or row.source_ids != row.statement.source_ids:
                    raise ValueError("Представление изменило утверждение или его источники")
        if self.presentation.originals != self.insight.sources:
            raise ValueError("Оригиналы должны совпадать с источниками инсайта")
        expected = list(statements)
        if len(set(seen)) != len(seen) or set(seen) != set(expected):
            raise ValueError("Каждое утверждение должно попасть ровно в одну группу")
        if {b.id for b in self.presentation.blocks} != {
            "event",
            "relevance",
            "consequences",
            "actions",
            "bank",
            "basis",
        }:
            raise ValueError("Нужны шесть различных блоков")
        return self
