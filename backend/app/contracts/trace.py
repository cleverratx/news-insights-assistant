"""Публичный журнал действий: цели шагов и основания, без скрытых рассуждений LLM."""

from typing import Literal, Self

from pydantic import AwareDatetime, Field, HttpUrl, JsonValue, model_validator

from app.agent.context import ContextObservation
from app.agent.tools import ToolError
from app.contracts.common import SCHEMA_VERSION, Contract, Id, Text, require_unique
from app.contracts.critic import CriticReport
from app.contracts.generation import ExecutionStatus, GenerationUsage
from app.contracts.insight import SourceRef, SourceType
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
    usage: GenerationUsage

    @model_validator(mode="after")
    def references(self) -> Self:
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
        available = {d.ref_id for d in self.documents} | {f.id for f in self.profile_features}
        if any(s.ref_id not in available for s in self.used_sources):
            raise ValueError("Основание вывода отсутствует в снимке")
        if self.finished_at < self.started_at:
            raise ValueError("Время завершения раньше начала")
        return self
