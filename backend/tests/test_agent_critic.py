from typing import Any

import pytest

from app.agent.context import AnalysisContext
from app.agent.critic import InsightCritic
from app.agent.orchestrator import Limits, Orchestrator
from app.agent.tools import Decision
from app.agent.tools.builtin import create_tools
from app.contracts.insight import CheckProblem
from tests.test_agent_builtin_tools import Database
from tests.test_agent_orchestrator import ScriptedPlanner
from tests.test_agent_tools import Replies
from tests.test_rank_rules import profile


def context(db: Database, bank: bool = True) -> AnalysisContext:
    return AnalysisContext(
        profile=profile(), news=[db.news, *([db.bank] if bank else [])], objective="Влияние события"
    )


def draft(db: Database) -> dict[str, Any]:
    return {
        "company_id": "U_1",
        "news_ids": [db.news.id],
        "what_happened": [
            {"id": "event", "text": "Экспорт угля вырос", "origin": "fact", "source_ids": ["n"]}
        ],
        "why_it_matters": [
            {
                "id": "why",
                "text": "Важна логистика сырья для отрасли компании",
                "origin": "model",
                "source_ids": ["n", "p"],
            }
        ],
        "risks": [
            {
                "id": "risk",
                "text": "Возможно изменение стоимости перевозок",
                "origin": "model",
                "source_ids": ["n", "p"],
            }
        ],
        "actions": [
            {
                "id": "action",
                "text": "Уточнить ставки перевозки у поставщиков",
                "kind": "question",
                "origin": "model",
                "source_ids": ["n"],
            }
        ],
        "bank_view": [
            {
                "id": "bank",
                "text": "Банк ожидает сохранения ставки",
                "origin": "bank",
                "source_ids": ["b"],
            }
        ],
        "sources": [
            {
                "id": "n",
                "type": "news",
                "ref_id": db.news.id,
                "title": db.news.title,
                "quote": db.news.fragments[0].text,
            },
            {
                "id": "p",
                "type": "profile_feature",
                "ref_id": "feature:industry",
                "title": "Отрасль",
            },
            {
                "id": "b",
                "type": "bank_analytics",
                "ref_id": db.bank.id,
                "title": db.bank.title,
                "fragment_id": db.bank.fragments[1].id,
                "quote": db.bank.fragments[1].text,
            },
        ],
        "self_check": {"passed": True, "iterations": 999, "issues": []},
    }


async def test_valid_draft_requires_semantic_check_and_self_check_is_not_trusted() -> None:
    db = Database()
    llm = Replies('{"issues":[]}')
    result = await InsightCritic(llm).review(context(db), draft(db))
    assert result.passed and result.semantic_checked and result.cost_rub > 0
    assert len(llm.messages) == 1
    assert "self_check" not in llm.messages[0][1].content
    unavailable = await InsightCritic().review(context(db), draft(db))
    assert not unavailable.passed and unavailable.unavailable


@pytest.mark.parametrize(
    "case", ["quote", "fragment", "url", "source", "type", "company", "event", "citation"]
)
async def test_fabricated_basis_and_cross_company_are_rejected_before_llm(case: str) -> None:
    db = Database()
    value = draft(db)
    if case == "quote":
        value["sources"][0]["quote"] = "Выручка выросла на 300%"
    elif case == "fragment":
        value["sources"][2]["fragment_id"] = "missing"
    elif case == "url":
        value["sources"][0]["url"] = "https://fabricated.test/article"
    elif case == "source":
        value["sources"][2]["ref_id"] = "bank:missing"
    elif case == "type":
        value["sources"][0]["type"] = "bank_analytics"
    elif case == "company":
        value["company_id"] = "U_other"
    elif case == "event":
        value["news_ids"] = ["news:other"]
    elif case == "citation":
        value["what_happened"][0]["source_ids"] = ["missing"]
    llm = Replies()
    report = await InsightCritic(llm).review(context(db), value)
    assert not report.passed and report.issues and llm.messages == []


@pytest.mark.parametrize(
    "block", ["what_happened", "why_it_matters", "risks", "actions", "bank_view"]
)
async def test_required_blocks_are_checked(block: str) -> None:
    db = Database()
    value = draft(db)
    value[block] = []
    report = await InsightCritic(Replies()).review(context(db), value)
    assert not report.passed


async def test_missing_source_list_and_source_ids_are_not_accepted() -> None:
    db = Database()
    for value in ({"answer": "ok"}, draft(db)):
        if "sources" in value:
            value["what_happened"][0]["source_ids"] = []
        report = await InsightCritic(Replies()).review(context(db), value)
        assert not report.passed


async def test_unpersonalized_reason_is_detected() -> None:
    db = Database()
    value = draft(db)
    value["why_it_matters"][0]["source_ids"] = ["n"]
    report = await InsightCritic(Replies()).review(context(db), value)
    assert any(i.problem is CheckProblem.NOT_PERSONAL for i in report.issues)


async def test_semantic_contradiction_is_recorded_and_cannot_be_marked_resolved_by_model() -> None:
    db = Database()
    reply = (
        '{"issues":[{"statement_id":"bank","problem":"contradiction",'
        '"detail":"Цифра противоречит источнику"}]}'
    )
    report = await InsightCritic(Replies(reply)).review(context(db), draft(db))
    assert not report.passed and report.semantic_checked
    assert report.issues[0].problem is CheckProblem.CONTRADICTION and not report.issues[0].resolved


@pytest.mark.parametrize(
    "reply",
    ["not JSON", "{}", '{"issues":[{"statement_id":"unknown","problem":"other","detail":"x"}]}'],
)
async def test_invalid_semantic_reply_is_not_a_pass(reply: str) -> None:
    db = Database()
    report = await InsightCritic(Replies(reply)).review(context(db), draft(db))
    assert not report.passed and report.unavailable and report.cost_rub > 0


async def test_failed_review_triggers_original_lookup_and_then_rechecks_draft() -> None:
    db = Database()
    value = draft(db)
    done = Decision(action="finish", reason="Черновик", output=value)
    planner = ScriptedPlanner(
        done,
        Decision(
            action="tool",
            tool="get_original",
            reason="Проверить источник банка",
            arguments={"doc_id": db.bank.id, "fragment_id": db.bank.fragments[1].id},
        ),
        done,
    )
    llm = Replies('{"issues":[]}')
    result = await Orchestrator(planner, create_tools(db), critic=InsightCritic(llm)).run(
        profile(),
        [db.news],
        "Проверить событие",
    )
    assert result.status == "completed"
    assert len(result.context.critic_reviews) == 2
    assert not result.context.critic_reviews[0].report.passed
    assert result.context.critic_reviews[1].report.passed
    assert planner.contexts[1].critic_reviews[0].report.suggested_tools
    assert result.context.steps[0].decision.tool == "get_original"
    assert result.output["self_check"]["iterations"] == 2
    assert len(llm.messages) == 1  # Структурно неверный черновик не тратит LLM критика.


async def test_repeated_bad_drafts_stop_without_exposing_unchecked_output() -> None:
    db = Database()
    done = Decision(action="finish", reason="Черновик", output={"answer": "unsupported"})
    planner = ScriptedPlanner(done, done, done)
    result = await Orchestrator(planner, create_tools(db), critic=InsightCritic(Replies())).run(
        profile(),
        [db.news],
        "Проверить",
    )
    assert result.status == "check_failed" and result.output is None
    assert len(result.context.critic_reviews) == len(planner.contexts) == 2


async def test_review_repair_does_not_reset_step_limit() -> None:
    db = Database()
    done = Decision(action="finish", reason="Черновик", output={"answer": "unsupported"})
    result = await Orchestrator(
        ScriptedPlanner(done, done),
        create_tools(db),
        Limits(max_steps=1),
        critic=InsightCritic(Replies()),
    ).run(
        profile(),
        [db.news],
        "Проверить",
    )
    assert result.status == "step_limit" and result.output is None
    assert len(result.context.critic_reviews) == 1


async def test_semantic_unavailability_returns_check_failed_without_repeat() -> None:
    db = Database()
    done = Decision(action="finish", reason="Черновик", output=draft(db))
    result = await Orchestrator(
        ScriptedPlanner(done),
        create_tools(db),
        critic=InsightCritic(Replies(RuntimeError("private-key"))),
    ).run(
        profile(),
        [db.news, db.bank],
        "Проверить",
    )
    assert result.status == "check_failed" and result.output is None
    assert "private-key" not in result.model_dump_json()


async def test_empty_bank_search_allows_honest_absence_of_bank_view() -> None:
    from app.agent.tools import Observation
    from app.contracts.search import SearchResult

    db = Database()
    ctx = context(db, bank=False)
    empty = SearchResult(query="ставка", hits=[], reranked=False, searched=0)
    ctx.remember(
        "search_bank_analytics",
        {"query": "ставка"},
        Observation(data=empty.model_dump(mode="json")),
        1,
        True,
    )
    value = draft(db)
    value["bank_view"] = []
    value["sources"] = value["sources"][:2]
    assert (await InsightCritic(Replies('{"issues":[]}')).review(ctx, value)).passed


async def test_common_deadline_also_cancels_semantic_critic() -> None:
    import asyncio
    from collections.abc import Sequence

    from app.llm.models import LLMResult, Message, Task

    cancelled = asyncio.Event()

    class SlowCritic:
        async def complete(self, task: Task, messages: Sequence[Message]) -> LLMResult:
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
            raise AssertionError("Ожидание должно быть отменено")

    db = Database()
    done = Decision(action="finish", reason="Черновик", output=draft(db))
    result = await Orchestrator(
        ScriptedPlanner(done),
        create_tools(db),
        Limits(timeout_seconds=0.05),
        critic=InsightCritic(SlowCritic()),
    ).run(profile(), [db.news, db.bank], "Проверить")
    assert result.status == "timeout" and result.output is None and cancelled.is_set()


async def test_duplicate_semantic_json_keys_cannot_hide_a_problem() -> None:
    db = Database()
    reply = '{"issues":[{"problem":"contradiction","detail":"x"}],"issues":[]}'
    report = await InsightCritic(Replies(reply)).review(context(db), draft(db))
    assert report.unavailable and not report.passed and report.cost_rub > 0


async def test_semantic_review_cost_is_included_in_total_run_cost() -> None:
    from decimal import Decimal

    db = Database()
    done = Decision(action="finish", reason="Черновик", output=draft(db))
    result = await Orchestrator(
        ScriptedPlanner(done),
        create_tools(db),
        critic=InsightCritic(Replies('{"issues":[]}')),
    ).run(profile(), [db.news, db.bank], "Проверить")
    assert result.status == "completed"
    assert (
        result.context.cost_rub
        == Decimal("0.01") + result.context.critic_reviews[0].report.cost_rub
    )
    assert result.output["self_check"]["iterations"] == 1


async def test_unrecognized_output_block_is_not_silently_approved() -> None:
    db = Database()
    value = draft(db)
    value["unsupported_claims"] = ["Не подтверждённый источником вывод"]
    report = await InsightCritic(Replies()).review(context(db), value)
    assert not report.passed
