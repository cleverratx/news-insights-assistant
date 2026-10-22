"""A5-04: единый контекст компании и события, наблюдения и память одного разбора."""

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue

from app.agent.tools import Decision, Observation, ToolResponse
from app.contracts.common import Id, Text
from app.contracts.critic import CriticReport
from app.contracts.news import NewsItem
from app.contracts.profile import CompanyProfile


class Step(ToolResponse):
    model_config = ConfigDict(extra="forbid", frozen=True)
    number: int
    decision: Decision
    elapsed_seconds: float
    reused_from: Id | None = None
    observation_id: Id | None = None


class ContextObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: Id
    tool: Id
    arguments: dict[str, JsonValue]
    observation: Observation
    step_number: int = Field(ge=1)


class CriticReview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    draft: JsonValue
    report: CriticReport


class AnalysisContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: Id = Field(default_factory=lambda: f"run:{uuid4().hex}")
    objective: Text
    started_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    profile: CompanyProfile
    news: list[NewsItem] = Field(min_length=1)
    steps: list[Step] = Field(default_factory=list)
    observations: dict[str, ContextObservation] = Field(default_factory=dict)
    critic_reviews: list[CriticReview] = Field(default_factory=list)
    output_schema: dict[str, JsonValue] | None = None
    result_cache: dict[str, str] = Field(default_factory=dict, repr=False)
    remaining_steps: int = Field(default=0, ge=0)
    remaining_seconds: float = Field(default=0, ge=0, allow_inf_nan=False)
    cost_rub: Decimal = Field(default=Decimal(0), ge=0, allow_inf_nan=False)

    def known_source_ids(self) -> list[str]:
        return sorted(
            {
                *(feature.id for feature in self.profile.features),
                *(item.id for item in self.news),
                *(
                    source
                    for entry in self.observations.values()
                    for source in entry.observation.source_ids
                ),
            }
        )

    def cached(self, tool: str, arguments: dict[str, JsonValue]) -> ContextObservation | None:
        observation_id = self.result_cache.get(call_key(tool, arguments))
        entry = self.observations.get(observation_id or "")
        return entry.model_copy(deep=True) if entry is not None else None

    def remember(
        self,
        tool: str,
        arguments: dict[str, JsonValue],
        observation: Observation,
        step_number: int,
        cacheable: bool,
    ) -> str:
        observation_id = f"observation:{step_number}"
        self.observations[observation_id] = ContextObservation(
            id=observation_id,
            tool=tool,
            arguments=arguments,
            observation=observation,
            step_number=step_number,
        ).model_copy(deep=True)
        if cacheable and reusable(observation):
            self.result_cache[call_key(tool, arguments)] = observation_id
        return observation_id

    def planner_data(self) -> dict[str, Any]:
        """Результат передаётся один раз, шаги ссылаются на него по id."""
        observations = []
        profile_data = self.profile.model_dump(mode="json")
        for entry in self.observations.values():
            value = entry.model_dump(mode="json")
            if entry.tool == "get_profile" and entry.observation.data == profile_data:
                # Профиль уже целиком передан в общем контексте.
                value["observation"]["data"] = {"context_ref": "profile"}
            observations.append(value)
        return {
            "run_id": self.run_id,
            "objective": self.objective,
            "started_at": self.started_at.isoformat(),
            "profile": profile_data,
            "news": [item.model_dump(mode="json") for item in self.news],
            "steps": [step.model_dump(mode="json", exclude={"observation"}) for step in self.steps],
            "observations": observations,
            "known_source_ids": self.known_source_ids(),
            "critic_reviews": [review.model_dump(mode="json") for review in self.critic_reviews],
            "output_schema": self.output_schema,
        }


def call_key(tool: str, arguments: dict[str, JsonValue]) -> str:
    value = json.dumps([tool, arguments], sort_keys=True, ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(value.encode()).hexdigest()


def reusable(observation: Observation) -> bool:
    data = observation.data
    if isinstance(data, dict):
        status = data.get("status")
        if isinstance(status, str) and status in {"not_found", "unavailable"}:
            return False
        if "hits" in data and not data["hits"]:
            return False
    return True
