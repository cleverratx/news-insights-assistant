from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

from app.api.companies import get_connection
from app.contracts.knowledge import FactSource, FactStatus
from app.data.loader import read_case, store
from app.main import create_app
from app.profile.builder import assemble
from app.profile.gaps import SLOTS, answer, gaps
from app.profile.graph import CompanyLinks, LinkedCompany, Payment
from app.profile.knowledge import draft, resolve
from app.profile.row import CompanyRow
from tests.test_data_loader import database
from tests.test_samples import COMPANIES, LINKS

__all__ = ["database"]

T0 = datetime(2026, 10, 21, 9, tzinfo=UTC)
IMPORTER = CompanyRow(
    company_id="U_1",
    okved_code="46.38",
    okved_name="Торговля оптовая рыбой",
    revenue_rub=Decimal("18200000000"),
    region="Москва",
    has_loan=True,
    has_customs_records=True,
    customs_countries=["КИТАЙ", "ТУРЦИЯ"],
)


def report(row: CompanyRow = IMPORTER) -> Any:
    profile = assemble(row, CompanyLinks())
    return gaps(profile, resolve(row.company_id, draft(profile, T0)))


def questions(result: Any) -> dict[str, Any]:
    return {question.key: question for question in result.questions}


def test_questions_are_personal_and_explain_why() -> None:
    found = questions(report())
    export = found["markets.export_share"]
    assert "(Китай, Турция)" in export.question
    assert export.priority == "high"
    assert export.based_on == ["feature:customs"]
    assert "курса" in export.why
    assert "регионе Москва" in found["markets.sales_regions"].question
    assert "«Продукты питания и напитки»" in found["seasonality.peak"].question
    assert found["seasonality.peak"].priority == "high"
    assert "«Торговля оптовая рыбой»" in found["products.main"].question
    assert "финансирование" in found["plans.next_year"].question
    assert [question.priority for question in report().questions] == sorted(
        (question.priority for question in report().questions),
        key=["high", "medium", "low"].index,
    )


def test_no_export_question_without_foreign_trade() -> None:
    row = IMPORTER.model_copy(update={"has_customs_records": False, "customs_countries": []})
    assert "markets.export_share" not in questions(report(row))


def test_non_seasonal_segment_gets_low_priority() -> None:
    row = IMPORTER.model_copy(update={"okved_code": "62.01", "okved_name": None})
    assert questions(report(row))["seasonality.peak"].priority == "low"


def test_empty_company_still_gets_questions() -> None:
    result = report(CompanyRow(company_id="U_0"))
    assert {question.key for question in result.questions} == {slot.key for slot in SLOTS} - {
        "markets.export_share"
    }
    assert all(question.based_on == [] for question in result.questions)
    assert result.to_confirm == []


def test_assumptions_go_to_confirmation_not_questions() -> None:
    result = report()
    assert result.to_confirm
    assert all(fact.status is FactStatus.ASSUMPTION for fact in result.to_confirm)
    assert "markets.trade_direction" in {fact.key for fact in result.to_confirm}
    assert result.known == 0


def test_answer_closes_question_and_counts_as_known() -> None:
    profile = assemble(IMPORTER, CompanyLinks())
    base = resolve("U_1", draft(profile, T0))
    reply = answer(base, "seasonality.peak", "Пик — декабрь", now=T0)
    assert (reply.source, reply.status, reply.version) == (
        FactSource.CLIENT,
        FactStatus.CONFIRMED,
        1,
    )
    after = gaps(profile, resolve("U_1", [*base.facts, reply]))
    assert "seasonality.peak" not in questions(after)
    assert after.known == 1


def test_answer_to_drafted_key_revises_the_draft() -> None:
    links = CompanyLinks(
        payments=[Payment(LinkedCompany("U_7", okved_code="56.10"), "incoming", Decimal(5), 1)]
    )
    profile = assemble(IMPORTER, links)
    base = resolve("U_1", draft(profile, T0))
    drafted = next(fact for fact in base.facts if fact.key == "customers.main")
    reply = answer(base, "customers.main", "Рестораны, 80% выручки", now=T0)
    assert (reply.id, reply.version, reply.source) == (drafted.id, 2, FactSource.CLIENT)


def test_unknown_question_key() -> None:
    base = resolve("U_1", [])
    with pytest.raises(KeyError):
        answer(base, "unknown.slot", "ответ", now=T0)


@pytest.fixture
async def api(database: asyncpg.Connection) -> AsyncIterator[AsyncClient]:
    await store(database, read_case(COMPANIES, LINKS))
    app = create_app()

    async def override() -> AsyncIterator[Any]:
        yield database

    app.dependency_overrides[get_connection] = override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def test_questions_endpoint_and_answers(api: AsyncClient) -> None:
    base = "/api/companies/U_900001/knowledge"
    first = (await api.get(f"{base}/questions")).json()
    keys = [question["key"] for question in first["questions"]]
    assert keys[0] in {"markets.sales_regions", "markets.export_share", "seasonality.peak"}
    assert "customers.main" not in keys
    assert "customers.main" in {fact["key"] for fact in first["to_confirm"]}

    reply = await api.post(
        f"{base}/questions/markets.export_share/answer",
        json={"text": "Экспорта нет, 70% закупок — импорт", "value": 0.0},
    )
    assert reply.status_code == 201
    skipped = await api.post(
        f"{base}/questions/plans.next_year/answer", json={"not_applicable": True}
    )
    assert skipped.json()["text"] == "Не относится к компании"
    revised = await api.post(
        f"{base}/questions/customers.main/answer", json={"text": "Рестораны и сети, около 80%"}
    )
    assert revised.json()["id"] == "draft:U_900001:customers.main"
    assert revised.json()["version"] == 2

    after = (await api.get(f"{base}/questions")).json()
    left = {question["key"] for question in after["questions"]}
    assert {"markets.export_share", "plans.next_year"} & left == set()
    assert "customers.main" not in {fact["key"] for fact in after["to_confirm"]}
    assert after["known"] == 3

    profile = (await api.get("/api/companies/U_900001/profile")).json()
    labels = {feature["label"] for feature in profile["features"] if feature["kind"] == "knowledge"}
    assert "Со слов клиента: Экспорта нет, 70% закупок — импорт" in labels


async def test_answer_errors(api: AsyncClient) -> None:
    base = "/api/companies/U_900001/knowledge/questions"
    assert (await api.post(f"{base}/nope/answer", json={"text": "x"})).status_code == 404
    assert (await api.post(f"{base}/plans.next_year/answer", json={})).status_code == 422
    assert (
        await api.post(
            "/api/companies/U_404/knowledge/questions/plans.next_year/answer", json={"text": "x"}
        )
    ).status_code == 404
    assert (await api.get("/api/companies/U_404/knowledge/questions")).status_code == 404
