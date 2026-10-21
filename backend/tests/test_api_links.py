from typing import Any

from httpx import AsyncClient

from tests.test_api_knowledge import api, database

__all__ = ["api", "database"]

BASE = "/api/companies/U_900001"


def cards(response: Any) -> dict[str, dict[str, Any]]:
    return {card["id"]: card for card in response.json()["cards"]}


async def test_cards_from_payments_and_group(api: AsyncClient) -> None:
    response = await api.get(f"{BASE}/links")
    assert response.status_code == 200
    body = response.json()
    assert body["incoming"]["counterparties"] == 4
    found = cards(response)
    buyer = found["U_900201"]
    assert buyer["origins"] == ["payments"]
    assert (buyer["role"], buyer["role_source"]) == ("buyer", "payments")
    assert buyer["incoming"]["amount_share"] == 0.55
    assert found["U_900102"]["origins"] == ["payments", "group"]
    assert found["U_900101"]["origins"] == ["group"]
    assert found["U_900301"]["origins"] == ["shared_person"]


async def test_client_sets_role(api: AsyncClient) -> None:
    response = await api.put(f"{BASE}/links/U_900102/role", json={"role": "carrier"})
    assert response.status_code == 200
    card = response.json()
    assert (card["role"], card["role_source"]) == ("carrier", "client")

    fact_id = card["role_fact_id"]
    history = (await api.get(f"{BASE}/knowledge/history", params={"fact_id": fact_id})).json()
    assert [item["value"] for item in history] == ["carrier"]

    again = await api.put(f"{BASE}/links/U_900102/role", json={"role": "supplier"})
    assert again.json()["role_fact_id"] == fact_id
    history = (await api.get(f"{BASE}/knowledge/history", params={"fact_id": fact_id})).json()
    assert [item["version"] for item in history] == [1, 2]

    profile = (await api.get(f"{BASE}/profile")).json()
    labels = [item["label"] for item in profile["features"] if item["kind"] == "knowledge"]
    assert "Со слов клиента: U_900102 — поставщик" in labels


async def test_role_for_unknown_company(api: AsyncClient) -> None:
    response = await api.put(f"{BASE}/links/U_999999/role", json={"role": "buyer"})
    assert response.status_code == 404
    assert (await api.put(f"{BASE}/links/U_900201/role", json={"role": "x"})).status_code == 422
    missing = await api.put("/api/companies/U_0/links/U_900201/role", json={"role": "buyer"})
    assert missing.status_code == 404


async def test_client_adds_and_removes_company(api: AsyncClient) -> None:
    response = await api.post(
        f"{BASE}/links",
        json={"name": "ООО «Северный терминал»", "role": "carrier", "okved_code": "52.10"},
    )
    assert response.status_code == 201
    card = response.json()
    assert card["id"].startswith("manual:")
    assert (card["origins"], card["company_id"], card["segment"]) == (
        ["client"],
        None,
        "logistics",
    )
    assert cards(await api.get(f"{BASE}/links"))[card["id"]]["name"] == "ООО «Северный терминал»"

    profile = (await api.get(f"{BASE}/profile")).json()
    feature = next(
        item for item in profile["features"] if item["source_fields"][0].startswith("links:")
    )
    assert "перевозчик" in feature["label"]
    assert any(feature["id"] in item["feature_ids"] for item in profile["exposures"])

    changed = await api.put(f"{BASE}/links/{card['id']}/role", json={"role": "supplier"})
    assert changed.json()["role"] == "supplier"

    assert (await api.delete(f"{BASE}/links/{card['id']}")).status_code == 204
    assert card["id"] not in cards(await api.get(f"{BASE}/links"))
    assert (await api.delete(f"{BASE}/links/{card['id']}")).status_code == 404
    assert (await api.delete(f"{BASE}/links/U_900201")).status_code == 404


async def test_manual_company_needs_name_and_role(api: AsyncClient) -> None:
    assert (await api.post(f"{BASE}/links", json={"name": "", "role": "buyer"})).status_code == 422
    assert (await api.post(f"{BASE}/links", json={"name": "ООО «Ромашка»"})).status_code == 422
