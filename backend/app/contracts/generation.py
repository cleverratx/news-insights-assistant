from decimal import Decimal
from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, model_validator

from app.contracts.common import BankProduct, Contract, Id, Significance, Text
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
from app.contracts.related_feed import CompanyMention
from app.llm.models import AllowedModel

NOT_ADVICE = "Не является инвестиционной рекомендацией."
AI_MADE = (
    "Разбор подготовил ИИ по материалам ленты и аналитике Альфа-Банка. Позиция банка — только "
    "в разделе «Позиция Альфа-Банка», остальные выводы не являются официальной позицией банка."
)
AI_ONLY = (
    "Разбор подготовил ИИ. Аналитики Альфа-Банка по этой теме не найдено: выводы ИИ "
    "не являются официальной позицией банка."
)

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


class OfferAction(StrEnum):
    CONTACT_MANAGER = "contact_manager"
    APPLICATION = "application"
    CALLBACK = "callback"


class ProductOffer(Contract):
    """Продукт банка, уместный после этого события именно для этой компании."""

    product: BankProduct
    title: Text
    why: Text = Field(description="Связь события, вывода и признаков компании с продуктом")
    benefit: Text = Field(description="Что продукт даёт в этой ситуации")
    statement_ids: list[Id] = Field(
        min_length=1, description="Риски или возможности инсайта, на которые отвечает продукт"
    )
    feature_ids: list[Id] = Field(min_length=1, description="Признаки компании-основания")
    already_client: bool = Field(description="Продукт уже подключён: речь о пересмотре условий")
    next_actions: list[OfferAction] = Field(min_length=1)
    label: Text = "Предложение Альфа-Банка"
    disclaimer: Text


class RoleRewrite(Contract):
    statement_id: Id
    text: Text


class RoleView(Contract):
    """Инсайт глазами должности: формулировки и акценты, а фактура — общая для всех ролей."""

    role: Text
    title: Text = Field(description="Должность словами")
    focus: Text = Field(description="Что важно этой должности")
    method: Literal["llm", "rules"] = Field(
        description="llm — формулировки переписаны под роль и проверены; rules — только акценты"
    )
    headline: Text | None = None
    summary: Text | None = Field(default=None, description="Главное для этой должности")
    summary_ids: list[Id] = Field(default_factory=list)
    emphasis: list[Id] = Field(
        default_factory=list, description="Утверждения, которые роль увидит первыми"
    )
    risks: list[Id] = Field(default_factory=list, description="Порядок рисков для роли")
    opportunities: list[Id] = Field(default_factory=list)
    actions: list[Id] = Field(default_factory=list)
    rewrites: list[RoleRewrite] = Field(
        default_factory=list, description="Формулировки выводов модели под роль"
    )


class RelatedFrame(Contract):
    """Событие у конкурента или контрагента: выжимка новости и «что это значит для нас»."""

    company: CompanyMention
    relation_feature_id: Id = Field(description="Признак профиля со связью клиента и компании")
    relation: Text = Field(description="Кем компания приходится клиенту и на чём это основано")
    digest: list[Id] = Field(default_factory=list, description="Выжимка: что произошло у компании")
    meaning: list[Id] = Field(
        default_factory=list, description="Что это значит для нас: важность, последствия, действия"
    )


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
    related: RelatedFrame | None = Field(
        default=None, description="Разбор события у конкурента или контрагента"
    )
    role_view: RoleView | None = Field(
        default=None, description="Акценты и формулировки под должность поверх общей фактуры"
    )
    offer: ProductOffer | None = Field(
        default=None, description="Не больше одного продукта и только при обоснованной связи"
    )
    cache_hit: bool = Field(
        default=False,
        description="Сохранённый ранее разбор выдан из кеша инсайтов без обращения к LLM",
    )
    disclaimer: Text = Field(
        default=f"{AI_MADE} {NOT_ADVICE}",
        description="Обязательная пометка под разбором: кто составил и что это не рекомендация",
    )

    @model_validator(mode="after")
    def disclaimed(self) -> Self:
        if NOT_ADVICE not in self.disclaimer:
            raise ValueError("Разбор должен предупреждать, что это не инвестиционная рекомендация")
        return self

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
        if self.related is not None:
            insight = self.insight
            meaning = {
                s.id
                for s in [
                    *insight.why_it_matters,
                    *insight.risks,
                    *insight.opportunities,
                    *insight.actions,
                ]
            }
            if not set(self.related.digest) <= {s.id for s in insight.what_happened}:
                raise ValueError("Выжимка состоит из утверждений «что произошло»")
            if not set(self.related.meaning) <= meaning:
                raise ValueError("«Что это значит для нас» — важность, последствия и действия")
        if self.role_view is not None:
            view = self.role_view
            if self.insight.status is not InsightStatus.READY or view.role != self.role:
                raise ValueError("Взгляд роли — только для готового инсайта и той же должности")
            orders: list[tuple[list[str], list[str]]] = [
                (view.risks, [s.id for s in self.insight.risks]),
                (view.opportunities, [s.id for s in self.insight.opportunities]),
                (view.actions, [s.id for s in self.insight.actions]),
            ]
            for order, section in orders:
                if sorted(order) != sorted(section):
                    raise ValueError("Порядок роли должен переставлять утверждения раздела")
            model_ids = {
                s.id
                for s in [
                    *self.insight.why_it_matters,
                    *self.insight.risks,
                    *self.insight.opportunities,
                    *self.insight.actions,
                ]
                if s.origin is Origin.MODEL
            }
            rewritten = [r.statement_id for r in view.rewrites]
            if len(set(rewritten)) != len(rewritten) or not set(rewritten) <= model_ids:
                raise ValueError("Под роль переписываются только выводы модели, факты — нет")
            known = {s.id for s in self.insight.statements()}
            if not set(view.emphasis) | set(view.summary_ids) <= known:
                raise ValueError("Взгляд роли ссылается на неизвестные утверждения")
        if self.offer is not None:
            impacts = {s.id for s in [*self.insight.risks, *self.insight.opportunities]}
            if self.insight.status is not InsightStatus.READY:
                raise ValueError("Продукт предлагается только в готовом инсайте")
            if not set(self.offer.statement_ids) <= impacts:
                raise ValueError("Предложение должно опираться на риски или возможности инсайта")
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
