from decimal import Decimal
from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, model_validator

from app.contracts.common import Contract, Id, Significance, Text
from app.contracts.insight import (
    Action,
    Impact,
    Insight,
    InsightStatus,
    Origin,
    SourceRef,
    Statement,
)
from app.contracts.rank import ImpactAssessment, Importance, ScoreBreakdown
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


class GapKind(StrEnum):
    MODEL = "model"
    VERIFICATION = "verification"
    NUMBERS = "numbers"
    BANK_VIEW = "bank_view"
    SOURCE_TEXT = "source_text"
    COMPANY_DATA = "company_data"


class DataGap(Contract):
    """Чего не хватило для вывода и что из-за этого не показано или показано с оговоркой."""

    kind: GapKind
    missing: Text = Field(description="Чего не хватает — словами для пользователя")
    consequence: Text = Field(description="Что из-за этого не сделано или сделано с оговоркой")
    remedy: Text | None = Field(default=None, description="Как восполнить пробел")
    blocking: bool = Field(description="Из-за пробела персональный вывод не сформирован")
    details: list[Text] = Field(default_factory=list, max_length=10)
    question_keys: list[Id] = Field(
        default_factory=list, description="Вопросы базы знаний компании, закрывающие пробел"
    )
    news_ids: list[Id] = Field(default_factory=list)


class InsightRanking(Contract):
    news_id: Id
    score: ScoreBreakdown
    significance: Significance
    importance: Importance
    impact: ImpactAssessment


class GeneratedInsight(Contract):
    insight: Insight
    presentation: InsightPresentation
    usage: GenerationUsage
    execution_status: ExecutionStatus
    warnings: list[Text] = Field(default_factory=list)
    ranking: list[InsightRanking] = Field(default_factory=list)
    role: Text | None = None
    feed_snapshot_id: Id | None = None
    gaps: list[DataGap] = Field(
        default_factory=list, description="Чего не хватило: честный ответ вместо догадок"
    )
    cache_hit: bool = Field(
        default=False,
        description="Сохранённый ранее разбор выдан из кеша инсайтов без обращения к LLM",
    )

    @model_validator(mode="after")
    def separated(self) -> Self:
        if self.ranking:
            ids = [r.news_id for r in self.ranking]
            if len(set(ids)) != len(ids) or set(ids) != set(self.insight.news_ids):
                raise ValueError("Оценки должны соответствовать выбранным материалам")
            if any((r.score.role.id if r.score.role else None) != self.role for r in self.ranking):
                raise ValueError("Оценки относятся к другой должности")
        if self.insight.status is InsightStatus.READY and (
            not self.insight.self_check.passed or not self.insight.actions
        ):
            raise ValueError("Готовый инсайт должен пройти проверку и содержать действия")
        if self.insight.status is InsightStatus.READY and any(g.blocking for g in self.gaps):
            raise ValueError("У готового инсайта не может быть блокирующих пробелов")
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
        expected_blocks = {
            "event": [s.id for s in self.insight.what_happened],
            "relevance": [s.id for s in self.insight.why_it_matters],
            "consequences": [s.id for s in [*self.insight.risks, *self.insight.opportunities]],
            "actions": [s.id for s in self.insight.actions],
            "bank": [s.id for s in self.insight.bank_view],
            "basis": [],
        }
        for block in self.presentation.blocks:
            sources = [s.id for s in self.insight.sources] if block.id == "basis" else []
            if block.statement_ids != expected_blocks[block.id] or block.source_ids != sources:
                raise ValueError("Блок представления не соответствует разделу инсайта")
        return self
