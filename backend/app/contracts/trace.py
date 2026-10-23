"""Публичный журнал действий: цели шагов и основания, без скрытых рассуждений LLM."""

from typing import Literal, Self

from pydantic import AwareDatetime, Field, HttpUrl, JsonValue, model_validator

from app.agent.context import ContextObservation
from app.agent.tools import ToolError
from app.contracts.common import SCHEMA_VERSION, Contract, Id, Text, require_unique
from app.contracts.critic import CriticReport
from app.contracts.generation import ExecutionStatus, GenerationUsage, InsightRanking
from app.contracts.insight import Basis, Origin, SourceRef, SourceType, Support
from app.contracts.profile import ProfileFeature


class TraceStep(Contract):
    number: int = Field(ge=1)
    action: Literal["tool", "finish"]
    goal: Text
    tool: Id | None = None
    arguments: dict[str, JsonValue] = Field(default_factory=dict)
    status: Literal["ok", "error", "timeout"]
    elapsed_seconds: float = Field(ge=0, allow_inf_nan=False)
    planning_seconds: float = Field(ge=0, allow_inf_nan=False)
    check_index: int | None = Field(default=None, ge=0)
    observation_id: Id | None = None
    reused_from: Id | None = None
    error_detail: ToolError | None = None


class TraceChunk(Contract):
    fragment_id: Id | None = None
    text: str


class TraceDocument(Contract):
    ref_id: Id
    type: SourceType
    url: HttpUrl | None = None
    chunks: list[TraceChunk]


type Section = Literal[
    "what_happened", "why_it_matters", "risks", "opportunities", "actions", "bank_view"
]


class TraceClaim(Contract):
    """Утверждение итогового вывода и фрагменты, из которых оно получено."""

    statement_id: Id
    section: Section
    origin: Origin
    support: Support
    basis: list[Basis] = Field(default_factory=list)


class TraceRejection(Contract):
    """Утверждение черновика, которое проверка оспорила: что именно написала модель и почему
    это не показано как есть. Пользователь видит только число скрытых выводов и причины."""

    check_index: int = Field(ge=0)
    statement_id: Id
    section: Section | Literal["headline"]
    origin: Origin | None = None
    text: Text
    problems: list[Text] = Field(min_length=1)
    shown: bool = Field(description="Тот же текст попал в итоговый вывод после исправлений")


class AgentTrace(Contract):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    insight_id: Id
    run_id: Id
    company_id: Id
    news_ids: list[Id] = Field(min_length=1, max_length=3)
    objective: Text
    started_at: AwareDatetime
    finished_at: AwareDatetime
    execution_status: ExecutionStatus
    error: Text | None = None
    elapsed_seconds: float = Field(ge=0, allow_inf_nan=False)
    steps: list[TraceStep] = Field(default_factory=list)
    observations: list[ContextObservation] = Field(default_factory=list)
    checks: list[CriticReport] = Field(default_factory=list)
    documents: list[TraceDocument] = Field(default_factory=list)
    profile_features: list[ProfileFeature] = Field(default_factory=list)
    used_sources: list[SourceRef] = Field(default_factory=list)
    ranking: list[InsightRanking] = Field(default_factory=list)
    role: Text | None = None
    feed_snapshot_id: Id | None = None
    usage: GenerationUsage
    claims: list[TraceClaim] = Field(
        default_factory=list, description="Связь каждого утверждения с фрагментами документов"
    )
    rejected: list[TraceRejection] = Field(
        default_factory=list,
        description="Оспоренные проверкой утверждения черновиков: текст модели и замечания",
    )

    @model_validator(mode="after")
    def references(self) -> Self:
        if any(r.check_index >= len(self.checks) for r in self.rejected):
            raise ValueError("Отклонённое утверждение ссылается на неизвестную проверку")
        require_unique((str(s.number) for s in self.steps), "шагов")
        require_unique((o.id for o in self.observations), "наблюдений")
        require_unique((d.ref_id for d in self.documents), "документов")
        require_unique((f.id for f in self.profile_features), "признаков")
        require_unique((s.id for s in self.used_sources), "оснований")
        if any(s.check_index is not None and s.check_index >= len(self.checks) for s in self.steps):
            raise ValueError("Шаг ссылается на неизвестную проверку")
        observations = {o.id for o in self.observations}
        if any(
            ref not in observations
            for step in self.steps
            for ref in (step.observation_id, step.reused_from)
            if ref is not None
        ):
            raise ValueError("Шаг ссылается на неизвестное наблюдение")
        documents = {d.ref_id: d for d in self.documents}
        features = {f.id: f for f in self.profile_features}
        for source in self.used_sources:
            if source.type is SourceType.PROFILE_FEATURE:
                if source.ref_id not in features:
                    raise ValueError("Признак вывода отсутствует в снимке")
                continue
            document = documents.get(source.ref_id)
            if document is None or document.type is not source.type:
                raise ValueError("Тип или документ основания не соответствует снимку")
            chunks = [
                c
                for c in document.chunks
                if source.fragment_id is None or c.fragment_id == source.fragment_id
            ]
            if not chunks:
                raise ValueError("Фрагмент основания отсутствует в снимке")
            if source.quote is not None and not any(
                " ".join(source.quote.split()) in " ".join(chunk.text.split()) for chunk in chunks
            ):
                raise ValueError("Цитата основания не подтверждается снимком")
        require_unique((claim.statement_id for claim in self.claims), "утверждений")
        sources = {source.id: source for source in self.used_sources}
        for claim in self.claims:
            kinds = {basis.type for basis in claim.basis}
            if claim.support is Support.SOURCE and not kinds - {SourceType.PROFILE_FEATURE}:
                raise ValueError("Подтверждение источником требует фрагмента документа")
            if claim.support is Support.COMPANY_DATA and SourceType.PROFILE_FEATURE not in kinds:
                raise ValueError("Подтверждение данными компании требует признака")
            for basis in claim.basis:
                cited = sources.get(basis.source_id)
                if cited is None or (cited.type, cited.ref_id) != (basis.type, basis.ref_id):
                    raise ValueError("Основание утверждения не совпадает с источником вывода")
                if basis.type is SourceType.PROFILE_FEATURE:
                    if basis.ref_id not in features:
                        raise ValueError("Признак утверждения отсутствует в снимке")
                    continue
                document = documents.get(basis.ref_id)
                texts = [
                    " ".join(chunk.text.split())
                    for chunk in (document.chunks if document else [])
                    if basis.fragment_id is None or chunk.fragment_id == basis.fragment_id
                ]
                if not texts or (
                    basis.quote is not None
                    and not any(" ".join(basis.quote.split()) in text for text in texts)
                ):
                    raise ValueError("Фрагмент утверждения не подтверждается снимком")
        if self.finished_at < self.started_at:
            raise ValueError("Время завершения раньше начала")
        return self
