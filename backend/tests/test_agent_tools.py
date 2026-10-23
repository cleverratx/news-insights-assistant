import asyncio
import json
from collections.abc import Sequence
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.agent.orchestrator import AnalysisContext, Limits, LLMPlanner, Orchestrator
from app.agent.tools import (
    Decision,
    Observation,
    ToolProtocolError,
    ToolRegistry,
    ToolResponse,
    parse_decision,
)
from app.llm.models import LLMResult, Message, Task
from tests.test_agent_orchestrator import ScriptedPlanner, call, finish
from tests.test_news_classify import FakeLLM
from tests.test_rank_rules import item, profile


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    q: str = Field(min_length=1)
    limit: int = Field(default=5, ge=1, le=10)


class Result(BaseModel):
    model_config = ConfigDict(extra="forbid")
    count: int = Field(ge=0)


class Replies:
    def __init__(self, *texts: str | Exception) -> None:
        self.texts = iter(texts)
        self.messages: list[list[Message]] = []

    async def complete(self, task: Task, messages: Sequence[Message]) -> LLMResult:
        assert task is Task.INTERPRET
        self.messages.append(list(messages))
        reply = next(self.texts)
        if isinstance(reply, Exception):
            raise reply
        return await FakeLLM(reply).complete(task, messages)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "не JSON",
        '```json\n{"action":"finish","action":"tool","reason":"ok"}\n```',
        "[]",
        "null",
        '{"action":"tool","reason":"ok"}',
        '{"action":"finish","reason":"ok","tool":"lookup"}',
        '{"action":"finish","reason":"ok","output":NaN}',
        '{"action":"finish","reason":"ok","output":{"value":Infinity}}',
        '{"action":"finish","action":"tool","reason":"ok"}',
        '{"action":"finish","reason":"ok","output":{"x":1,"x":2}}',
        '{"action":"finish","reason":" ","extra":1}',
    ],
)
def test_malformed_decision_rejected(text: str) -> None:
    with pytest.raises(ToolProtocolError) as raised:
        parse_decision(text)
    assert raised.value.detail.code == "invalid_decision"
    assert "input" not in raised.value.detail.model_dump_json()


@pytest.mark.parametrize(
    "text",
    [
        '```json\n{"action":"finish","reason":"ok"}\n```',
        '```\n{"action":"finish","reason":"ok"}\n```',
        'Решение:\n{"action":"finish","reason":"ok"}\nГотово.',
    ],
)
def test_wrapped_decision_is_accepted(text: str) -> None:
    assert parse_decision(text) == Decision(action="finish", reason="ok")


def test_valid_decision_round_trip_and_response_invariants() -> None:
    assert parse_decision(call().model_dump_json()) == call()
    reply = ToolResponse(status="ok", observation=Observation(data={"count": 1}))
    assert ToolResponse.model_validate_json(reply.model_dump_json()) == reply
    with pytest.raises(ValidationError):
        ToolResponse(status="error", observation=Observation(), error="error")
    with pytest.raises(ValidationError):
        ToolResponse(status="ok")
    with pytest.raises(ValidationError):
        Observation(data={"nested": [float("nan")]})


def tools_with(handler: Any) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register("lookup", "Поиск", handler, arguments_model=Arguments, result_model=Result)
    return registry


async def test_argument_errors_do_not_call_handler_and_next_step_can_fix_them() -> None:
    calls = []

    async def handler(context: AnalysisContext, arguments: dict) -> Observation:
        calls.append(arguments)
        return Observation(data={"count": 1})

    bad = Decision(
        action="tool", reason="Поиск", tool="lookup", arguments={"limit": "private-value"}
    )
    planner = ScriptedPlanner(bad, call(), finish())
    result = await Orchestrator(planner, tools_with(handler)).run(profile(), [item()], "Проверить")
    assert result.status == "completed"
    assert calls == [{"q": "ставка", "limit": 5}]
    first = result.context.steps[0]
    assert first.error_detail is not None and first.error_detail.code == "invalid_arguments"
    assert {tuple(issue.path) for issue in first.error_detail.issues} == {("q",), ("limit",)}
    assert "private-value" not in first.error_detail.model_dump_json()
    assert planner.contexts[1].steps[0].error_detail == first.error_detail


@pytest.mark.parametrize(
    "arguments", [{"q": "ok", "limit": True}, {"q": "ok", "extra": 1}, {"q": 2}]
)
def test_strict_arguments(arguments: dict) -> None:
    registry = tools_with(None)
    with pytest.raises(ToolProtocolError) as raised:
        registry.validate_arguments("lookup", arguments)
    assert raised.value.detail.code == "invalid_arguments"


async def test_result_validation_does_not_publish_invalid_data() -> None:
    async def handler(context: AnalysisContext, arguments: dict) -> Observation:
        return Observation(data={"count": "private-value"})

    result = await Orchestrator(ScriptedPlanner(call(), finish()), tools_with(handler)).run(
        profile(),
        [item()],
        "Проверить",
    )
    step = result.context.steps[0]
    assert step.observation is None
    assert step.error_detail is not None and step.error_detail.code == "invalid_result"
    assert "private-value" not in step.model_dump_json()


def test_schema_discovery_and_registration_require_closed_objects() -> None:
    registry = tools_with(None)
    spec = registry.specs()[0]
    assert spec.arguments_schema["additionalProperties"] is False
    assert spec.arguments_schema["required"] == ["q"]
    assert "count" in spec.result_schema["properties"]
    spec.arguments_schema.clear()
    assert registry.specs()[0].arguments_schema
    with pytest.raises(ValueError):
        ToolRegistry().register("bad", "Незакрытый объект", None, arguments_model=BaseModel)


async def test_malformed_reply_is_corrected_and_all_attempts_are_charged() -> None:
    llm = Replies('{"action":"private-value"}', finish().model_dump_json())
    result = await Orchestrator(LLMPlanner(llm), ToolRegistry()).run(
        profile(), [item()], "Проверить"
    )
    assert result.status == "completed"
    assert len(llm.messages) == 2
    assert result.context.cost_rub == 2 * (await FakeLLM("x").complete(Task.INTERPRET, [])).cost_rub
    correction = llm.messages[1][-1].content
    assert "correction" in correction and "private-value" not in correction


async def test_unknown_tool_gets_correction_without_execution() -> None:
    llm = Replies(call("unknown").model_dump_json(), finish().model_dump_json())
    result = await Orchestrator(LLMPlanner(llm), ToolRegistry()).run(
        profile(), [item()], "Проверить"
    )
    assert result.status == "completed" and result.context.steps == []
    assert json.loads(llm.messages[1][-1].content)["error"]["code"] == "unknown_tool"


@pytest.mark.parametrize("max_steps,expected_calls", [(1, 1), (8, 2)])
async def test_repairs_are_bounded_by_step_budget(max_steps: int, expected_calls: int) -> None:
    llm = Replies("bad", "bad", "bad")
    result = await Orchestrator(LLMPlanner(llm), ToolRegistry(), Limits(max_steps=max_steps)).run(
        profile(),
        [item()],
        "Проверить",
    )
    assert result.status == "planner_error"
    assert len(llm.messages) == expected_calls
    assert result.context.cost_rub > 0


async def test_repair_consumes_step_and_cannot_execute_extra_tool() -> None:
    calls = []

    async def handler(context: AnalysisContext, arguments: dict) -> Observation:
        calls.append(arguments)
        return Observation(data={"count": 1})

    llm = Replies("bad", call().model_dump_json(), finish().model_dump_json())
    result = await Orchestrator(LLMPlanner(llm), tools_with(handler), Limits(max_steps=2)).run(
        profile(),
        [item()],
        "Проверить",
    )
    assert result.status == "step_limit"
    assert len(llm.messages) == 2 and len(calls) == 1
    assert result.context.steps[0].number == 2


async def test_provider_failure_during_repair_keeps_known_cost() -> None:
    llm = Replies("bad", RuntimeError("private-provider-key"))
    result = await Orchestrator(LLMPlanner(llm), ToolRegistry()).run(
        profile(), [item()], "Проверить"
    )
    assert result.status == "planner_error" and result.context.cost_rub > 0
    assert "private-provider-key" not in result.model_dump_json()


async def test_total_timeout_during_repair_preserves_paid_first_reply() -> None:
    class SlowRepair(Replies):
        async def complete(self, task: Task, messages: Sequence[Message]) -> LLMResult:
            if self.messages:
                await asyncio.Event().wait()
            return await super().complete(task, messages)

    llm = SlowRepair("bad")
    result = await Orchestrator(LLMPlanner(llm), ToolRegistry(), Limits(timeout_seconds=0.1)).run(
        profile(),
        [item()],
        "Проверить",
    )
    assert result.status == "timeout" and result.context.cost_rub > 0


async def test_tool_schemas_are_sent_to_planner() -> None:
    llm = Replies(finish().model_dump_json())
    result = await Orchestrator(LLMPlanner(llm), tools_with(None)).run(
        profile(), [item()], "Проверить"
    )
    assert result.status == "completed"
    payload = json.loads(llm.messages[0][1].content)
    [spec] = payload["tools"]
    assert spec["arguments_schema"]["required"] == ["q"]
    assert spec["result_fields"] == ["count"]


async def test_repairs_can_be_disabled() -> None:
    llm = Replies("bad", finish().model_dump_json())
    result = await Orchestrator(LLMPlanner(llm, max_repairs=0), ToolRegistry()).run(
        profile(), [item()], "Проверить"
    )
    assert result.status == "planner_error" and len(llm.messages) == 1


@pytest.mark.parametrize("repairs", [-1, 3, True])
def test_invalid_repair_limit_rejected(repairs: int) -> None:
    with pytest.raises(ValueError):
        LLMPlanner(Replies(), max_repairs=repairs)
