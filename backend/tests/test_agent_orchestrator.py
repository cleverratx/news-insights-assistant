import asyncio
from collections.abc import Sequence
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.agent.orchestrator import (
    AnalysisContext,
    Decision,
    Limits,
    LLMPlanner,
    Observation,
    Orchestrator,
    Planning,
    ToolRegistry,
    ToolSpec,
)
from tests.test_news_classify import FakeLLM
from tests.test_rank_rules import item, profile


class ScriptedPlanner:
    def __init__(self, *decisions: Decision) -> None:
        self.decisions = iter(decisions)
        self.contexts: list[AnalysisContext] = []

    async def next_step(self, context: AnalysisContext, tools: Sequence[ToolSpec]) -> Planning:
        self.contexts.append(context)
        return Planning(decision=next(self.decisions), cost_rub=Decimal("0.01"))


def call(name: str = "lookup") -> Decision:
    return Decision(
        action="tool", reason="Нужен первоисточник", tool=name, arguments={"q": "ставка"}
    )


def finish() -> Decision:
    return Decision(action="finish", reason="Данных достаточно", output={"answer": "Готово"})


async def lookup(context: AnalysisContext, arguments: dict) -> Observation:
    assert arguments["q"] == "ставка"
    return Observation(data={"rate": 10}, source_ids=["news:1"])


def registry() -> ToolRegistry:
    tools = ToolRegistry()
    tools.register("lookup", "Найти данные", lookup)
    return tools


async def test_plan_tool_replan_finish_shared_context_and_isolation() -> None:
    planner = ScriptedPlanner(call(), finish())
    agent = Orchestrator(planner, registry())
    company = profile()
    news = item()
    result = await agent.run(company, [news], "Оценить событие")
    assert result.status == "completed"
    assert result.output == {"answer": "Готово"}
    assert len(result.context.steps) == 1
    assert planner.contexts[1].steps[0].observation == Observation(
        data={"rate": 10},
        source_ids=["news:1"],
    )
    assert planner.contexts[0].steps == []
    assert planner.contexts[0].remaining_steps == 8
    assert planner.contexts[1].remaining_steps == 7
    assert 0 < planner.contexts[1].remaining_seconds <= 60
    assert result.context.cost_rub == Decimal("0.02")
    assert result.context.profile == company
    assert company == profile() and news == item()
    assert result.elapsed_seconds >= 0


async def test_error_observation_allows_planner_to_change_plan() -> None:
    async def bad(context: AnalysisContext, arguments: dict) -> Observation:
        raise ValueError("private-source-text")

    tools = registry()
    tools.register("bad", "Неисправный инструмент", bad)
    planner = ScriptedPlanner(call("bad"), call(), finish())
    result = await Orchestrator(planner, tools).run(profile(), [item()], "Проверить")
    assert result.status == "completed"
    assert result.context.steps[0].status == "error"
    assert planner.contexts[1].steps[0].error == "Инструмент не выполнил шаг"
    assert "private-source-text" not in result.model_dump_json()


async def test_unknown_tool_is_not_executed() -> None:
    result = await Orchestrator(ScriptedPlanner(call("unknown"), finish()), registry()).run(
        profile(),
        [item()],
        "Проверить",
    )
    assert result.context.steps[0].error == "Инструмент отсутствует в реестре"
    assert result.status == "completed"


async def test_step_limit_bounds_repeated_calls() -> None:
    planner = ScriptedPlanner(call(), call(), finish())
    result = await Orchestrator(planner, registry(), Limits(max_steps=2)).run(
        profile(),
        [item()],
        "Проверить",
    )
    assert result.status == "step_limit"
    assert len(result.context.steps) == len(planner.contexts) == 2
    assert result.output is None


async def test_total_deadline_includes_planning_and_cancels_it() -> None:
    cancelled = asyncio.Event()

    class SlowPlanner:
        async def next_step(self, context: AnalysisContext, tools: Sequence[ToolSpec]) -> Planning:
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
            return Planning(decision=finish())

    result = await Orchestrator(SlowPlanner(), registry(), Limits(timeout_seconds=0.02)).run(
        profile(),
        [item()],
        "Проверить",
    )
    assert result.status == "timeout" and cancelled.is_set()
    assert result.context.steps == []


async def test_tool_timeout_allows_recovery_and_total_timeout_stops_run() -> None:
    cancelled = asyncio.Event()

    async def slow(context: AnalysisContext, arguments: dict) -> Observation:
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
        return Observation()

    tools = registry()
    tools.register("slow", "Долгий поиск", slow)
    result = await Orchestrator(
        ScriptedPlanner(call("slow"), finish()),
        tools,
        Limits(timeout_seconds=1, tool_timeout_seconds=0.02),
    ).run(profile(), [item()], "Проверить")
    assert result.status == "completed" and cancelled.is_set()
    assert result.context.steps[0].status == "timeout"
    cancelled.clear()
    result = await Orchestrator(
        ScriptedPlanner(call("slow")),
        tools,
        Limits(timeout_seconds=0.02),
    ).run(profile(), [item()], "Проверить")
    assert result.status == "timeout" and cancelled.is_set()
    assert result.context.steps[0].status == "timeout"


async def test_external_cancellation_is_not_hidden() -> None:
    entered = asyncio.Event()

    async def slow(context: AnalysisContext, arguments: dict) -> Observation:
        entered.set()
        await asyncio.Event().wait()
        return Observation()

    tools = ToolRegistry()
    tools.register("lookup", "Поиск", slow)
    task = asyncio.create_task(
        Orchestrator(ScriptedPlanner(call()), tools).run(
            profile(),
            [item()],
            "Проверить",
        )
    )
    await asyncio.wait_for(entered.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_llm_planner_uses_existing_client_and_accounts_invalid_response() -> None:
    for text, expected in [(finish().model_dump_json(), "completed"), ("не JSON", "planner_error")]:
        llm = FakeLLM(text)
        result = await Orchestrator(LLMPlanner(llm), registry()).run(
            profile(), [item()], "Проверить"
        )
        assert result.status == expected
        assert result.context.cost_rub > 0


async def test_empty_registry_can_finish_and_runs_have_unique_contexts() -> None:
    agent = Orchestrator(ScriptedPlanner(finish(), finish()), ToolRegistry())
    first, second = await asyncio.gather(
        agent.run(profile(), [item()], "Первый"),
        agent.run(profile(), [item()], "Второй"),
    )
    assert first.status == second.status == "completed"
    assert first.context.run_id != second.context.run_id
    assert first.context.objective != second.context.objective
    assert first.context.steps == second.context.steps == []


def test_registry_rejects_duplicates() -> None:
    tools = registry()
    with pytest.raises(ValueError):
        tools.register("lookup", "Дубликат", lookup)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_steps": 0},
        {"max_steps": True},
        {"timeout_seconds": 0},
        {"timeout_seconds": float("inf")},
        {"tool_timeout_seconds": -1},
    ],
)
def test_limits_validated(kwargs: dict) -> None:
    with pytest.raises(ValidationError):
        Limits(**kwargs)


def test_decision_requires_consistent_action() -> None:
    with pytest.raises(ValidationError):
        Decision(action="tool", reason="Поиск")
    with pytest.raises(ValidationError):
        Decision(action="finish", reason="Итог", tool="lookup")


async def test_invalid_tool_result_becomes_observation_error() -> None:
    from typing import cast

    async def invalid(context: AnalysisContext, arguments: dict) -> Observation:
        return cast(Observation, {"unexpected": "data"})

    tools = ToolRegistry()
    tools.register("lookup", "Поиск", invalid)
    result = await Orchestrator(ScriptedPlanner(call(), finish()), tools).run(
        profile(),
        [item()],
        "Проверить",
    )
    assert result.status == "completed"
    assert result.context.steps[0].status == "error"
    assert result.context.steps[0].observation is None


async def test_planner_and_tool_mutations_do_not_rewrite_run_context() -> None:
    class MutatingPlanner(ScriptedPlanner):
        async def next_step(self, context: AnalysisContext, tools: Sequence[ToolSpec]) -> Planning:
            context.objective = "Посторонняя задача"
            context.steps.clear()
            return await super().next_step(context, tools)

    async def mutate(context: AnalysisContext, arguments: dict) -> Observation:
        context.objective = "Изменённая задача"
        context.profile.features.clear()
        arguments["nested"]["q"] = "Изменено"
        return Observation(data="ok")

    tools = ToolRegistry()
    tools.register("lookup", "Поиск", mutate)
    decision = Decision(
        action="tool", tool="lookup", reason="Поиск", arguments={"nested": {"q": "rate"}}
    )
    result = await Orchestrator(MutatingPlanner(decision, finish()), tools).run(
        profile(),
        [item()],
        "Исходная задача",
    )
    assert result.context.objective == "Исходная задача"
    assert result.context.profile.features
    assert len(result.context.steps) == 1
    assert result.context.steps[0].decision.arguments == {"nested": {"q": "rate"}}
