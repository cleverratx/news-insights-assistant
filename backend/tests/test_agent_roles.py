import json
from collections.abc import Sequence
from typing import Any

import pytest
from pydantic import ValidationError

from app.agent.insight import generate_with_trace
from app.agent.roles import Answer, Rewrite, arrange, vetted
from app.agent.tools import Decision
from app.api.feed import get_llm
from app.contracts.generation import GeneratedInsight
from app.contracts.insight import Insight
from app.llm.models import LLMResult, Message, Task
from app.rank.roles import Role
from tests.test_agent_critic import draft
from tests.test_agent_tools import Replies
from tests.test_agent_trace import Store
from tests.test_llm_cost import cache_client as cache_client
from tests.test_news_classify import FakeLLM
from tests.test_rank_rules import profile

CREDIT = "Рост ставки по кредитам повысит расходы на обслуживание долга"
SUPPLY = "Возможно изменение стоимости перевозок и сроков поставок"


def two_risks(db: Store) -> dict[str, Any]:
    value = draft(db)
    value["news_ids"] = [db.news.id, db.bank.id]
    value["risks"] = [
        {"id": "supply", "text": SUPPLY, "origin": "model", "source_ids": ["n", "p"]},
        {"id": "credit", "text": CREDIT, "origin": "model", "source_ids": ["n", "p"]},
    ]
    return value


def finish(value: dict[str, Any]) -> str:
    return Decision(action="finish", reason="Готово", output=value).model_dump_json()


class Desk:
    """Агент отвечает по сценарию, переформулировка под роль — отдельным ответом."""

    def __init__(self, interpret: Sequence[str], summarize: str | Exception) -> None:
        self.agent = Replies(*interpret)
        self.summarize = summarize
        self.tasks: list[Task] = []

    async def complete(self, task: Task, messages: Sequence[Message]) -> LLMResult:
        self.tasks.append(task)
        if task is Task.SUMMARIZE:
            if isinstance(self.summarize, Exception):
                raise self.summarize
            return await FakeLLM(self.summarize).complete(task, messages)
        return await self.agent.complete(task, messages)


def rewrite(**changes: Any) -> str:
    answer = {
        "headline": "Дорогие кредиты давят на финансовый результат",
        "summary": "Главное для финансов — расходы на обслуживание долга вырастут",
        "summary_ids": ["credit"],
        "rewrites": [
            {"id": "credit", "text": "Обслуживание долга подорожает вместе со ставкой"},
            {"id": "event", "text": "Экспорт угля вырос на 40%"},
        ],
    }
    return json.dumps({**answer, **changes}, ensure_ascii=False)


async def ready(db: Store, role: Role | None, summarize: str | Exception = "{}") -> Any:
    llm = Desk([finish(two_risks(db)), '{"issues":[]}'], summarize)
    result, trace = await generate_with_trace(profile(), [db.news, db.bank], db, llm, role=role)
    return result, trace, llm


async def base_insight() -> Insight:
    result, *_ = await ready(Store(), None)
    assert result.insight.status == "ready"
    return result.insight


async def test_roles_reorder_and_emphasise_by_their_concerns() -> None:
    insight = await base_insight()
    cfo, logistics = arrange(insight, Role.CFO), arrange(insight, Role.LOGISTICS)
    assert cfo.risks == ["credit", "supply"] and logistics.risks == ["supply", "credit"]
    assert cfo.emphasis[0] == "credit" and logistics.emphasis[0] == "supply"
    assert cfo.method == "rules" and cfo.title == "Финансовый директор"
    assert "долг" in cfo.focus


async def test_rewrites_keep_meaning_and_never_touch_facts_or_add_numbers() -> None:
    insight = await base_insight()
    base = arrange(insight, Role.CFO)
    view = vetted(Answer.model_validate_json(rewrite()), insight, base)
    assert view is not None and view.method == "llm"
    assert [(r.statement_id, r.text) for r in view.rewrites] == [
        ("credit", "Обслуживание долга подорожает вместе со ставкой")
    ]
    assert view.summary_ids == ["credit"]
    padded = Answer(
        headline="Ставка 25% разорит компанию",
        summary="Расходы вырастут на 30%",
        summary_ids=["credit"],
        rewrites=[Rewrite(id="credit", text="Расходы на долг вырастут на 15%")],
    )
    assert vetted(padded, insight, base) is None


async def test_role_view_uses_model_and_falls_back_to_rules() -> None:
    db = Store()
    result, trace, llm = await ready(db, Role.CFO, rewrite())
    view = result.role_view
    assert view is not None and view.method == "llm" and view.role == "cfo"
    assert view.headline == "Дорогие кредиты давят на финансовый результат"
    assert llm.tasks[-1] is Task.SUMMARIZE
    assert result.usage.calls == 3 and trace.usage == result.usage
    failed, *_ = await ready(Store(), Role.CFO, RuntimeError("модель недоступна"))
    assert failed.insight.status == "ready"
    assert failed.role_view is not None and failed.role_view.method == "rules"
    broken, *_ = await ready(Store(), Role.LOGISTICS, "не JSON")
    assert broken.role_view is not None and broken.role_view.method == "rules"
    plain, *_ = await ready(Store(), None)
    assert plain.role_view is None


async def test_fact_base_is_the_same_for_every_role() -> None:
    cfo, cfo_trace, _ = await ready(Store(), Role.CFO, rewrite())
    logistics, logistics_trace, _ = await ready(Store(), Role.LOGISTICS)
    assert cfo_trace.objective == logistics_trace.objective
    shared = {"id", "run_id", "created_at"}
    assert cfo.insight.model_dump(exclude=shared) == logistics.insight.model_dump(exclude=shared)
    assert cfo.ranking[0].score.role is not None and cfo.ranking[0].score.role.id == "cfo"
    assert cfo.role_view != logistics.role_view


async def test_contract_forbids_rewriting_facts_or_foreign_order() -> None:
    result, *_ = await ready(Store(), Role.CFO, rewrite())
    payload = result.model_dump(mode="json")
    payload["role_view"]["rewrites"].append({"statement_id": "event", "text": "Иначе"})
    with pytest.raises(ValidationError):
        GeneratedInsight.model_validate(payload)
    payload = result.model_dump(mode="json")
    payload["role_view"]["risks"] = ["credit"]
    with pytest.raises(ValidationError):
        GeneratedInsight.model_validate(payload)
    payload = result.model_dump(mode="json")
    payload["role_view"]["role"] = "ceo"
    with pytest.raises(ValidationError):
        GeneratedInsight.model_validate(payload)


async def test_switching_role_reuses_cached_analysis(cache_client: Any) -> None:  # noqa: F811
    http, db = cache_client
    app = http._transport.app
    first_llm = Desk([finish(two_risks(db)), '{"issues":[]}'], rewrite())
    app.dependency_overrides[get_llm] = lambda: first_llm
    body = {"news_ids": [db.news.id, db.bank.id], "role": "cfo"}
    first = (await http.post("/api/companies/U_1/insights", json=body)).json()
    assert first["role_view"]["role"] == "cfo" and not first["cache_hit"]
    second_llm = Desk([], "{}")
    app.dependency_overrides[get_llm] = lambda: second_llm
    response = await http.post("/api/companies/U_1/insights", json={**body, "role": "logistics"})
    second = response.json()
    assert second["cache_hit"] and second["insight"] == first["insight"]
    assert second["role"] == "logistics" and second["role_view"]["role"] == "logistics"
    assert second["role_view"]["risks"][0] == "supply"
    assert all(r["score"]["role"]["id"] == "logistics" for r in second["ranking"])
    assert Task.INTERPRET not in second_llm.tasks
    assert len(db.saved) == 1
    again = (await http.post("/api/companies/U_1/insights", json=body)).json()
    assert again["role_view"] == first["role_view"]
