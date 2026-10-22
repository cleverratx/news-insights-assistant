from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict, Field

from app.agent.context import AnalysisContext, call_key
from app.agent.orchestrator import Limits, Orchestrator
from app.agent.tools import Decision, Observation, ToolRegistry
from tests.test_agent_orchestrator import ScriptedPlanner, call, finish
from tests.test_rank_rules import item, profile


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    q: str
    limit: int = Field(default=5, ge=1)


def registry(handler: Any, cacheable: bool = True) -> ToolRegistry:
    tools = ToolRegistry()
    tools.register("lookup", "Поиск", handler, arguments_model=Arguments, cacheable=cacheable)
    return tools


async def test_repeated_calls_reuse_normalized_arguments_and_preserve_sources() -> None:
    calls = []

    async def handler(context: AnalysisContext, arguments: dict) -> Observation:
        calls.append(arguments)
        return Observation(data={"answer": "Данные"}, source_ids=["bank:1"])

    explicit = call().model_copy(update={"arguments": {"limit": 5, "q": "ставка"}})
    planner = ScriptedPlanner(call(), explicit, finish())
    result = await Orchestrator(planner, registry(handler)).run(profile(), [item()], "Проверить")
    assert result.status == "completed" and calls == [{"q": "ставка", "limit": 5}]
    first, second = result.context.steps
    assert first.observation_id == second.observation_id == second.reused_from
    assert first.reused_from is None
    assert len(result.context.observations) == 1
    assert first.observation == second.observation
    assert "bank:1" in result.context.known_source_ids()
    assert "bank:1" in planner.contexts[-1].known_source_ids()
    assert len(result.context.steps) == 2


@pytest.mark.parametrize("cacheable,expected", [(False, 2), (True, 1)])
async def test_only_explicitly_cacheable_tools_are_reused(cacheable: bool, expected: int) -> None:
    calls = []

    async def handler(context: AnalysisContext, arguments: dict) -> Observation:
        calls.append(arguments)
        return Observation(data="ok")

    result = await Orchestrator(
        ScriptedPlanner(call(), call(), finish()), registry(handler, cacheable)
    ).run(
        profile(),
        [item()],
        "Проверить",
    )
    assert result.status == "completed" and len(calls) == expected


async def test_failed_call_is_retried_and_only_success_is_reused() -> None:
    calls = []

    async def handler(context: AnalysisContext, arguments: dict) -> Observation:
        calls.append(arguments)
        if len(calls) == 1:
            raise RuntimeError("Временный сбой")
        return Observation(data="ok")

    result = await Orchestrator(
        ScriptedPlanner(call(), call(), call(), finish()), registry(handler)
    ).run(
        profile(),
        [item()],
        "Проверить",
    )
    assert len(calls) == 2
    assert [s.status for s in result.context.steps] == ["error", "ok", "ok"]
    assert len(result.context.observations) == 1
    assert result.context.steps[2].reused_from == "observation:2"


@pytest.mark.parametrize(
    "data",
    [
        {"status": "unavailable"},
        {"status": "not_found"},
        {"hits": []},
    ],
)
async def test_missing_data_is_not_cached(data: Any) -> None:
    calls = []

    async def handler(context: AnalysisContext, arguments: dict) -> Observation:
        calls.append(arguments)
        return Observation(data=data)

    result = await Orchestrator(ScriptedPlanner(call(), call(), finish()), registry(handler)).run(
        profile(),
        [item()],
        "Проверить",
    )
    assert result.status == "completed" and len(calls) == 2
    assert result.context.result_cache == {}
    assert len(result.context.observations) == 2


async def test_changed_arguments_do_not_reuse_previous_observation() -> None:
    calls = []

    async def handler(context: AnalysisContext, arguments: dict) -> Observation:
        calls.append(arguments)
        return Observation(data=arguments)

    other = call().model_copy(update={"arguments": {"q": "инфляция"}})
    result = await Orchestrator(ScriptedPlanner(call(), other, finish()), registry(handler)).run(
        profile(),
        [item()],
        "Проверить",
    )
    assert len(calls) == len(result.context.observations) == 2
    assert result.context.steps[1].reused_from is None


async def test_memory_is_isolated_between_runs_on_same_orchestrator() -> None:
    calls = []

    async def handler(context: AnalysisContext, arguments: dict) -> Observation:
        calls.append(context.run_id)
        return Observation(data="ok")

    agent = Orchestrator(ScriptedPlanner(call(), finish(), call(), finish()), registry(handler))
    first = await agent.run(profile(), [item()], "Первый")
    second = await agent.run(profile(company_id="U_other"), [item()], "Второй")
    assert len(calls) == 2 and first.context.run_id != second.context.run_id
    assert first.context.steps[0].reused_from is second.context.steps[0].reused_from is None
    assert second.context.profile.company_id == "U_other"


async def test_prompt_references_one_observation_and_context_serializes() -> None:
    async def handler(context: AnalysisContext, arguments: dict) -> Observation:
        return Observation(data={"quote": "Единственная цитата"}, source_ids=["news:source"])

    result = await Orchestrator(ScriptedPlanner(call(), call(), finish()), registry(handler)).run(
        profile(),
        [item()],
        "Проверить",
    )
    restored = AnalysisContext.model_validate_json(result.context.model_dump_json())
    assert restored == result.context
    assert restored.cached("lookup", {"limit": 5, "q": "ставка"}) is not None
    payload = restored.planner_data()
    assert len(payload["observations"]) == 1
    assert all("observation" not in step for step in payload["steps"])
    assert payload["steps"][1]["observation_id"] == payload["observations"][0]["id"]
    assert "news:source" in payload["known_source_ids"]


async def test_reused_calls_still_consume_steps() -> None:
    async def handler(context: AnalysisContext, arguments: dict) -> Observation:
        return Observation(data="ok")

    result = await Orchestrator(
        ScriptedPlanner(call(), call(), finish()),
        registry(handler),
        Limits(max_steps=2),
    ).run(profile(), [item()], "Проверить")
    assert result.status == "step_limit" and len(result.context.steps) == 2
    assert result.context.steps[1].reused_from is not None


def test_call_keys_sort_objects_but_keep_tool_and_list_order() -> None:
    assert call_key("lookup", {"b": 2, "a": 1}) == call_key("lookup", {"a": 1, "b": 2})
    assert call_key("a", {}) != call_key("b", {})
    assert call_key("lookup", {"ids": [1, 2]}) != call_key("lookup", {"ids": [2, 1]})


async def test_profile_observation_is_a_reference_to_the_shared_profile() -> None:
    from app.agent.tools.builtin import create_tools

    decision = Decision(action="tool", tool="get_profile", reason="Прочитать профиль")
    result = await Orchestrator(ScriptedPlanner(decision, finish()), create_tools(None)).run(
        profile(),
        [item()],
        "Проверить",
    )
    [entry] = result.context.planner_data()["observations"]
    assert entry["observation"]["data"] == {"context_ref": "profile"}
    assert result.context.steps[0].observation is not None
    assert result.context.steps[0].observation.data == profile().model_dump(mode="json")


async def test_timed_out_call_is_not_reused() -> None:
    import asyncio

    calls = []

    async def handler(context: AnalysisContext, arguments: dict) -> Observation:
        calls.append(arguments)
        if len(calls) == 1:
            await asyncio.Event().wait()
        return Observation(data="ok")

    result = await Orchestrator(
        ScriptedPlanner(call(), call(), call(), finish()),
        registry(handler),
        Limits(tool_timeout_seconds=0.02),
    ).run(profile(), [item()], "Проверить")
    assert len(calls) == 2
    assert [step.status for step in result.context.steps] == ["timeout", "ok", "ok"]
    assert result.context.steps[-1].reused_from == "observation:2"


def test_cached_observation_is_a_copy_and_arbitrary_status_is_valid_json() -> None:
    ctx = AnalysisContext(profile=profile(), news=[item()], objective="Проверить")
    ctx.remember("lookup", {"q": "x"}, Observation(data={"status": {"value": 1}}), 1, True)
    cached = ctx.cached("lookup", {"q": "x"})
    assert cached is not None
    cached.observation.source_ids.append("fake-source")
    assert "fake-source" not in ctx.known_source_ids()


async def test_search_tools_share_the_run_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.agent.tools.builtin import create_tools
    from app.contracts.search import SearchResult

    moments = []

    async def search(connection: Any, query: str, **kwargs: Any) -> SearchResult:
        moments.append(kwargs["now"])
        return SearchResult(query=query, reranked=False, searched=0)

    monkeypatch.setattr("app.agent.tools.builtin.search", search)
    ctx = AnalysisContext(profile=profile(), news=[item()], objective="Проверить")
    tools = create_tools(None)
    for name in ("search_news", "search_bank_analytics"):
        args = tools.validate_arguments(name, {"query": "ставка"})
        await tools.handler(name)(ctx, args)
    assert moments == [ctx.started_at, ctx.started_at]


async def test_handler_cannot_mutate_the_cache_key_arguments() -> None:
    calls = []

    async def handler(context: AnalysisContext, arguments: dict) -> Observation:
        calls.append(arguments["q"])
        arguments["q"] = "Изменение внутри обработчика"
        return Observation(data="ok")

    result = await Orchestrator(ScriptedPlanner(call(), call(), finish()), registry(handler)).run(
        profile(),
        [item()],
        "Проверить",
    )
    assert calls == ["ставка"]
    assert result.context.cached("lookup", {"q": "ставка", "limit": 5}) is not None
    assert result.context.steps[1].reused_from == "observation:1"
