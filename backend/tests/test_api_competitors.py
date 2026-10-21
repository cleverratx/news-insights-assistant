from typing import Any

import pytest
from httpx import AsyncClient

from app.profile.legend import Legends, parse_legends
from tests.test_api_knowledge import api, database

__all__ = ["api", "database"]

BASE = "/api/companies/U_900001"
LEGENDS = parse_legends(
    """
companies:
  - synthetic_u: U_900001
    name: ООО «Рыбный двор»
    competitors:
      - name: Черкизово
        url: https://www.wikidata.org/wiki/Q4513187
        region: Москва
"""
)


@pytest.fixture(autouse=True)
def legends(monkeypatch: pytest.MonkeyPatch) -> Legends:
    for module in ("app.profile.competitors", "app.api.companies", "app.profile.builder"):
        monkeypatch.setattr(f"{module}.get_legends", lambda: LEGENDS)
    return LEGENDS


def ids(response: Any, field: str = "competitors") -> list[str]:
    return [item["id"] for item in response.json()[field]]


async def test_candidates_from_bank_and_open_sources(api: AsyncClient) -> None:
    response = await api.get(f"{BASE}/competitors")
    assert response.status_code == 200
    found = response.json()["competitors"]
    assert found[0]["id"] == "wikidata:Q4513187"
    assert found[0]["source"] == "open_source"
    bank = [item for item in found if item["source"] == "bank_data"]
    assert bank and bank[0]["id"] == "U_900504"
    assert bank[0]["reasons"][0].startswith("Тот же")
    assert all(item["id"] != "U_900001" for item in found)

    profile = (await api.get(f"{BASE}/profile")).json()
    feature = next(item for item in profile["features"] if item["kind"] == "competitor")
    assert feature["label"] == "Конкуренты на вашем рынке: Черкизово"


async def test_client_adds_and_removes(api: AsyncClient) -> None:
    response = await api.post(f"{BASE}/competitors", json={"name": "ООО «Морепродукт Трейд»"})
    assert response.status_code == 201
    added = response.json()
    assert added["source"] == "client" and added["id"].startswith("client:")
    assert ids(await api.get(f"{BASE}/competitors"))[0] == added["id"]

    profile = (await api.get(f"{BASE}/profile")).json()
    exposure = next(
        item for item in profile["exposures"] if item["feature_ids"] == ["feature:competitors"]
    )
    assert "Морепродукт Трейд" in exposure["keywords"]

    assert (await api.delete(f"{BASE}/competitors/{added['id']}")).status_code == 204
    after = await api.get(f"{BASE}/competitors")
    assert added["id"] not in ids(after) + ids(after, "excluded")


async def test_client_excludes_and_restores(api: AsyncClient) -> None:
    target = "wikidata:Q4513187"
    assert (await api.delete(f"{BASE}/competitors/{target}")).status_code == 204
    after = await api.get(f"{BASE}/competitors")
    assert target not in ids(after)
    assert ids(after, "excluded") == [target]
    profile = (await api.get(f"{BASE}/profile")).json()
    assert not [item for item in profile["features"] if item["kind"] == "competitor"]

    restored = await api.post(f"{BASE}/competitors/{target}/restore")
    assert restored.status_code == 200
    assert restored.json()["fact_id"] is None
    assert target in ids(await api.get(f"{BASE}/competitors"))

    assert (await api.delete(f"{BASE}/competitors/{target}")).status_code == 204
    assert ids(await api.get(f"{BASE}/competitors"), "excluded") == [target]


async def test_unknown_competitor_and_company(api: AsyncClient) -> None:
    assert (await api.delete(f"{BASE}/competitors/U_0")).status_code == 404
    assert (await api.get("/api/companies/U_0/competitors")).status_code == 404
    assert (await api.post(f"{BASE}/competitors", json={"name": ""})).status_code == 422
