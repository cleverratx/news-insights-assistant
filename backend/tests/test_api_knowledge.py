from collections.abc import AsyncIterator
from typing import Any

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

from app.api.companies import get_connection
from app.contracts.knowledge import FactCategory, FactSource
from app.data.loader import read_case, store
from app.main import create_app
from app.profile.builder import assemble
from app.profile.graph import CompanyLinks
from app.profile.knowledge import apply_knowledge, new_fact, resolve
from app.profile.row import CompanyRow
from tests.test_data_loader import database
from tests.test_samples import COMPANIES, LINKS

__all__ = ["database"]

BASE = "/api/companies/U_900001"
DIRECTION = "draft:U_900001:markets.trade_direction"


@pytest.fixture
async def api(database: asyncpg.Connection) -> AsyncIterator[AsyncClient]:
    await store(database, read_case(COMPANIES, LINKS))
    app = create_app()

    async def override() -> AsyncIterator[Any]:
        yield database

    app.dependency_overrides[get_connection] = override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


def facts(response: Any) -> dict[str, dict[str, Any]]:
    return {fact["key"] or fact["id"]: fact for fact in response.json()["facts"]}


async def test_draft_is_visible_as_assumptions(api: AsyncClient) -> None:
    response = await api.get(f"{BASE}/knowledge")
    assert response.status_code == 200
    found = facts(response)
    assert found["markets.trade_direction"]["status"] == "assumption"
    assert found["markets.trade_direction"]["source"] == "system"
    assert found["customers.main"]["source"] == "bank_data"


async def test_client_adds_fact(api: AsyncClient) -> None:
    response = await api.post(
        f"{BASE}/knowledge/facts",
        json={"category": "seasonality", "text": "Пик продаж — декабрь", "key": "seasonality.peak"},
    )
    assert response.status_code == 201
    fact = response.json()
    assert (fact["source"], fact["status"], fact["version"]) == ("client", "confirmed", 1)
    assert facts(await api.get(f"{BASE}/knowledge"))["seasonality.peak"]["id"] == fact["id"]

    profile = (await api.get(f"{BASE}/profile")).json()
    feature = next(item for item in profile["features"] if item["kind"] == "knowledge")
    assert feature["label"] == "Со слов клиента: Пик продаж — декабрь"
    assert feature["source_fields"] == [f"knowledge:{fact['id']}"]


async def test_client_correction_changes_profile_at_once(api: AsyncClient) -> None:
    before = (await api.get(f"{BASE}/profile")).json()
    fx_before = next(item for item in before["exposures"] if item["topic"] == "fx")
    assert fx_before["direction"] == "risk"

    response = await api.patch(
        f"{BASE}/knowledge/facts/{DIRECTION}",
        json={"text": "Мы экспортируем рыбу в Китай", "value": "export", "expected_version": 1},
    )
    assert response.status_code == 200
    edited = response.json()
    assert (edited["source"], edited["status"], edited["version"]) == ("client", "confirmed", 2)

    after = (await api.get(f"{BASE}/profile")).json()
    fx = next(item for item in after["exposures"] if item["topic"] == "fx")
    assert fx["direction"] == "opportunity"
    assert "подтвердил клиент: Мы экспортируем рыбу в Китай" in fx["reason"]
    assert fx["feature_ids"][0] == "feature:knowledge:markets.trade_direction"

    again = facts(await api.get(f"{BASE}/knowledge"))["markets.trade_direction"]
    assert again["value"] == "export"
    history = (await api.get(f"{BASE}/knowledge/history", params={"fact_id": DIRECTION})).json()
    assert [item["version"] for item in history] == [1, 2]
    assert [item["source"] for item in history] == ["system", "client"]


async def test_confirm_marks_fact_as_client(api: AsyncClient) -> None:
    fact_id = "draft:U_900001:company.size"
    response = await api.post(f"{BASE}/knowledge/facts/{fact_id}/confirm")
    assert response.status_code == 200
    assert (response.json()["source"], response.json()["status"]) == ("client", "confirmed")


async def test_delete_keeps_history_and_draft_does_not_return(api: AsyncClient) -> None:
    fact_id = "draft:U_900001:markets.regions"
    assert (await api.delete(f"{BASE}/knowledge/facts/{fact_id}")).status_code == 204
    assert "markets.regions" not in facts(await api.get(f"{BASE}/knowledge"))
    assert "markets.regions" not in facts(await api.get(f"{BASE}/knowledge"))
    history = (await api.get(f"{BASE}/knowledge/history", params={"fact_id": fact_id})).json()
    assert [item["deleted"] for item in history] == [False, True]
    response = await api.patch(f"{BASE}/knowledge/facts/{fact_id}", json={"text": "Москва"})
    assert response.status_code == 409


async def test_stale_version_is_conflict(api: AsyncClient) -> None:
    await api.get(f"{BASE}/knowledge")
    response = await api.patch(
        f"{BASE}/knowledge/facts/{DIRECTION}", json={"text": "Импорт", "expected_version": 5}
    )
    assert response.status_code == 409
    assert "текущая версия 1" in response.json()["detail"]


async def test_not_found_cases(api: AsyncClient) -> None:
    assert (await api.get("/api/companies/U_404/knowledge")).status_code == 404
    assert (
        await api.patch(f"{BASE}/knowledge/facts/fact:nope", json={"text": "x"})
    ).status_code == 404
    await api.get("/api/companies/U_900002/knowledge")
    foreign = "draft:U_900002:company.size"
    assert (await api.delete(f"{BASE}/knowledge/facts/{foreign}")).status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {"category": "markets", "text": ""},
        {"category": "unknown", "text": "x"},
        {"category": "markets", "text": "x", "source": "system"},
    ],
)
async def test_invalid_body_rejected(api: AsyncClient, body: dict[str, Any]) -> None:
    assert (await api.post(f"{BASE}/knowledge/facts", json=body)).status_code == 422


def test_apply_knowledge_without_client_facts_changes_nothing() -> None:
    row = CompanyRow(company_id="U_1", okved_code="46.38", has_customs_records=True)
    profile = assemble(row, CompanyLinks())
    system = new_fact(
        "U_1",
        FactCategory.MARKETS,
        "Вероятно импортёр",
        FactSource.SYSTEM,
        key="markets.trade_direction",
        value="import",
        evidence=["feature:trade_direction"],
    )
    assert apply_knowledge(profile, resolve("U_1", [system])) == profile


def test_client_direction_adds_fx_when_missing() -> None:
    profile = assemble(CompanyRow(company_id="U_1", okved_code="62.01"), CompanyLinks())
    assert all(item.topic != "fx" for item in profile.exposures)
    told = new_fact(
        "U_1",
        FactCategory.MARKETS,
        "Продаём лицензии в Казахстан",
        FactSource.CLIENT,
        key="markets.trade_direction",
        value="export",
    )
    applied = apply_knowledge(profile, resolve("U_1", [told]))
    fx = next(item for item in applied.exposures if item.topic == "fx")
    assert fx.direction == "opportunity"
    assert fx.feature_ids == ["feature:knowledge:markets.trade_direction"]
