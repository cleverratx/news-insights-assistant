from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import asyncpg
import pytest

from app.agent.orchestrator import AnalysisContext, Orchestrator
from app.agent.tools import Decision, ToolProtocolError
from app.agent.tools.builtin import CounterpartyGraph, SourceExcerpt, create_tools
from app.contracts.news import MaterialKind
from app.contracts.profile import CompanyProfile
from app.contracts.search import SearchResult
from app.profile.exposure import PaymentExposure
from app.profile.graph import PAYMENTS_SQL
from tests.test_agent_orchestrator import ScriptedPlanner, finish
from tests.test_rank_rules import profile
from tests.test_related_feed import linked
from tests.test_search import NEWS, RATE


def payment(
    partner: str, amount: str, direction: str = "incoming", code: str | None = "06.10"
) -> dict:
    return {
        "partner_id": partner,
        "amount_rub": Decimal(amount),
        "direction": direction,
        "okved_code": code,
        "okved_name": None,
        "region": None,
        "revenue_rub": None,
    }


class Database:
    def __init__(self) -> None:
        self.news = NEWS.model_copy(update={"published_at": datetime.now(UTC)})
        self.bank = RATE.model_copy(update={"published_at": datetime.now(UTC) - timedelta(days=60)})
        self.payments = [
            payment("U_2", "10.10"),
            payment("U_3", "20.20", code=None),
            payment("U_4", "900", "outgoing"),
        ]
        self.calls: list[tuple[str, tuple]] = []

    async def execute(self, sql: str, *args: Any) -> None:
        self.calls.append((sql, args))

    async def fetch(self, sql: str, *args: Any) -> list[dict]:
        self.calls.append((sql, args))
        if sql == PAYMENTS_SQL:
            assert args == ("U_1",)
            return self.payments
        if "FROM analytics_documents" in sql:
            return [{"item": self.bank.model_dump_json()}]
        if "FROM news_items" in sql:
            return [{"item": self.news.model_dump_json()}]
        raise AssertionError(sql)

    async def fetchrow(self, sql: str, *args: Any) -> dict | None:
        self.calls.append((sql, args))
        if "llm_cache" in sql:
            return None
        material = self.bank if "analytics_documents" in sql else self.news
        return {"item": material.model_dump_json()} if args[0] == material.id else None


def context(database: Database) -> AnalysisContext:
    return AnalysisContext(profile=profile(), news=[database.news], objective="Проверить событие")


async def invoke(database: Database, name: str, arguments: dict) -> Any:
    tools = create_tools(database)
    args = tools.validate_arguments(name, arguments)
    observation = await tools.handler(name)(context(database), args)
    return tools.validate_result(name, observation)


def test_all_six_tools_publish_input_and_output_schemas() -> None:
    specs = create_tools(Database()).specs()
    assert {s.name for s in specs} == {
        "get_profile",
        "search_news",
        "search_bank_analytics",
        "get_counterparty_graph",
        "calculate_exposure",
        "get_original",
    }
    assert all(s.arguments_schema["additionalProperties"] is False for s in specs)
    assert all(s.result_schema["additionalProperties"] is False for s in specs)


async def test_profile_comes_from_current_context_without_database() -> None:
    db = Database()
    observation = await invoke(db, "get_profile", {})
    result = CompanyProfile.model_validate(observation.data)
    assert result == profile()
    assert observation.source_ids == [f.id for f in result.features]
    assert db.calls == []


@pytest.mark.parametrize(
    "name,query,kind",
    [
        ("search_news", "экспорт угля Китай", MaterialKind.NEWS),
        ("search_bank_analytics", "ключевая ставка", MaterialKind.BANK_ANALYTICS),
    ],
)
async def test_search_separates_news_and_bank_and_preserves_quotes(
    name: str,
    query: str,
    kind: MaterialKind,
) -> None:
    db = Database()
    observation = await invoke(db, name, {"query": query})
    result = SearchResult.model_validate(observation.data)
    assert result.hits
    assert all(hit.kind is kind for hit in result.hits)
    for hit in result.hits:
        original = db.news if kind is MaterialKind.NEWS else db.bank
        assert hit.quote in original.text
        assert hit.doc_id in observation.source_ids
        assert hit.fragment_id in {f.id for f in original.fragments}
    assert not result.reranked


async def test_graph_uses_company_from_context(monkeypatch: pytest.MonkeyPatch) -> None:
    async def graph(connection: Any, company_id: str) -> Any:
        assert company_id == "U_1"
        return linked()

    monkeypatch.setattr("app.agent.tools.builtin.linked_companies", graph)
    observation = await invoke(Database(), "get_counterparty_graph", {})
    result = CounterpartyGraph.model_validate(observation.data)
    assert result.status == "available" and result.graph is not None
    assert result.graph.cards[0].name == "РЖД"


async def test_missing_graph_has_no_fabricated_zero_balances(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def graph(connection: Any, company_id: str) -> Any:
        raise asyncpg.UndefinedTableError()

    monkeypatch.setattr("app.agent.tools.builtin.linked_companies", graph)
    result = CounterpartyGraph.model_validate(
        (await invoke(Database(), "get_counterparty_graph", {})).data
    )
    assert result.status == "unavailable" and result.graph is None and result.warnings


async def test_exposure_uses_full_direction_decimal_amounts_and_unknown_segments() -> None:
    observation = await invoke(
        Database(), "calculate_exposure", {"direction": "incoming", "segments": ["oil_gas"]}
    )
    result = PaymentExposure.model_validate(observation.data)
    assert result.total_amount_rub == Decimal("30.30")
    assert result.matched_amount_rub == Decimal("10.10")
    assert result.share == pytest.approx(1 / 3)
    assert result.unknown_segment_amount_rub == Decimal("20.20") and result.warnings
    assert result.matched_partners == ["U_2"]


async def test_exposure_filters_are_combined_and_zero_turnover_is_unknown_share() -> None:
    db = Database()
    result = PaymentExposure.model_validate(
        (
            await invoke(
                db,
                "calculate_exposure",
                {
                    "direction": "incoming",
                    "partner_ids": ["U_3"],
                    "segments": ["oil_gas"],
                },
            )
        ).data
    )
    assert result.matched_amount_rub == 0 and result.share == 0
    db.payments = []
    empty = PaymentExposure.model_validate(
        (
            await invoke(
                db,
                "calculate_exposure",
                {
                    "direction": "incoming",
                },
            )
        ).data
    )
    assert empty.share is None and empty.warnings


async def test_original_from_context_and_bank_fragment_from_database() -> None:
    db = Database()
    current = SourceExcerpt.model_validate(
        (await invoke(db, "get_original", {"doc_id": db.news.id})).data
    )
    assert current.text == db.news.text and db.calls == []
    fragment = db.bank.fragments[1]
    saved = await invoke(db, "get_original", {"doc_id": db.bank.id, "fragment_id": fragment.id})
    result = SourceExcerpt.model_validate(saved.data)
    assert result.status == "found" and result.kind is MaterialKind.BANK_ANALYTICS
    assert result.text == fragment.text and result.locator == fragment.locator
    assert saved.source_ids == [db.bank.id]


async def test_missing_document_fragment_and_explicit_truncation() -> None:
    db = Database()
    for args in ({"doc_id": "news:missing"}, {"doc_id": db.news.id, "fragment_id": "missing"}):
        observation = await invoke(db, "get_original", args)
        assert SourceExcerpt.model_validate(observation.data).status == "not_found"
        assert observation.source_ids == []
    db.news = db.news.model_copy(update={"text": "А" * 200})
    result = SourceExcerpt.model_validate(
        (await invoke(db, "get_original", {"doc_id": db.news.id, "max_chars": 100})).data
    )
    assert result.truncated and result.text == "А" * 100


@pytest.mark.parametrize(
    "name,args",
    [
        ("get_profile", {"company_id": "U_other"}),
        ("get_original", {"url": "http://localhost"}),
        ("calculate_exposure", {"direction": "sideways"}),
        ("search_news", {"query": " "}),
        ("search_news", {"query": "курс", "days": 15}),
    ],
)
def test_invalid_arguments_and_customer_switch_are_rejected(name: str, args: dict) -> None:
    with pytest.raises(ToolProtocolError):
        create_tools(Database()).validate_arguments(name, args)


async def test_registered_tools_work_in_orchestrator_end_to_end() -> None:
    db = Database()
    decisions = [
        Decision(action="tool", reason="Профиль", tool="get_profile"),
        Decision(
            action="tool",
            reason="Искать",
            tool="search_bank_analytics",
            arguments={"query": "ключевая ставка"},
        ),
        Decision(
            action="tool", reason="Оригинал", tool="get_original", arguments={"doc_id": db.bank.id}
        ),
        Decision(
            action="tool",
            reason="Доля",
            tool="calculate_exposure",
            arguments={"direction": "incoming"},
        ),
        finish(),
    ]
    result = await Orchestrator(ScriptedPlanner(*decisions), create_tools(db)).run(
        profile(), [db.news], "Проверить"
    )
    assert result.status == "completed"
    assert all(step.status == "ok" for step in result.context.steps)
    assert [step.number for step in result.context.steps] == [1, 2, 3, 4]


async def test_builtin_schemas_fit_default_llm_input_limit() -> None:
    import json
    from collections.abc import Sequence

    from app.agent.orchestrator import LLMPlanner
    from app.core.config import Settings
    from app.llm.models import Message, Task
    from tests.test_agent_tools import Replies

    class BoundedClient(Replies):
        async def complete(self, task: Task, messages: Sequence[Message]) -> Any:
            payload = [message.model_dump() for message in messages]
            assert (
                len(json.dumps(payload, ensure_ascii=False).encode())
                < Settings(_env_file=None).llm_max_input_bytes
            )
            return await super().complete(task, messages)

    db = Database()
    result = await Orchestrator(
        LLMPlanner(BoundedClient(finish().model_dump_json())), create_tools(db)
    ).run(profile(), [db.news], "Проверить")
    assert result.status == "completed"


async def test_original_missing_storage_and_parameterized_document_id() -> None:
    class Missing(Database):
        async def fetchrow(self, sql: str, *args: Any) -> None:
            assert "DROP" not in sql
            assert args == ("news:'; DROP TABLE news_items;--",)
            raise asyncpg.UndefinedTableError()

    result = SourceExcerpt.model_validate(
        (
            await invoke(
                Missing(),
                "get_original",
                {
                    "doc_id": "news:'; DROP TABLE news_items;--",
                },
            )
        ).data
    )
    assert result.status == "unavailable" and result.text is None and result.warnings


async def test_exposure_includes_partners_beyond_top_cards() -> None:
    db = Database()
    db.payments = [payment(f"U_{i}", "1.01") for i in range(50)]
    result = PaymentExposure.model_validate(
        (
            await invoke(
                db,
                "calculate_exposure",
                {
                    "direction": "incoming",
                    "partner_ids": ["U_49"],
                },
            )
        ).data
    )
    assert result.total_amount_rub == Decimal("50.50")
    assert result.matched_amount_rub == Decimal("1.01") and result.share == pytest.approx(0.02)
