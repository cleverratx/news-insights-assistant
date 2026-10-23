from collections.abc import AsyncIterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

from app.api.companies import get_connection
from app.contracts.news import Topic
from app.main import create_app
from app.profile.builder import Peers, assemble
from app.profile.graph import CompanyLinks
from app.profile.row import CompanyRow
from tests.test_data_loader import HEADER, company_line, database, write

__all__ = ["database"]

FULL = CompanyRow(
    company_id="U_1",
    okved_code="46.38",
    okved_name="Торговля оптовая прочими пищевыми продуктами",
    revenue_rub=Decimal("18200000000"),
    region="Москва",
    city="г. Москва",
    client_type="CLIENT",
    has_loan=True,
    has_leasing=True,
    has_customs_records=True,
    customs_countries=["КИТАЙ"],
    group_id="g_1",
)


def test_empty_row_still_gives_valid_profile() -> None:
    profile = assemble(CompanyRow(company_id="U_0"), CompanyLinks())
    assert profile.features == []
    assert profile.industry.segment == "other"
    assert "MAINOKVED_CODE" in profile.data_quality.missing_fields
    assert profile.market_position is None


def test_full_row_profile_is_traceable() -> None:
    profile = assemble(FULL, CompanyLinks(), Peers("okved_code", 340, 3, Decimal("150000000000")))
    features = {feature.id: feature for feature in profile.features}
    assert features["feature:industry"].label == (
        "Отрасль: Продукты питания и напитки (ОКВЭД 46.38)"
    )
    assert features["feature:market_rank"].label == (
        "3-е место по выручке среди 340 компаний с ОКВЭД 46.38 в базе банка"
    )
    assert all(feature.source_fields for feature in profile.features)
    assert profile.market_position is not None
    assert profile.market_position.revenue_percentile == 0.9941
    assert round(profile.market_position.peer_revenue_share, 3) == 0.121
    topics = [exposure.topic for exposure in profile.exposures]
    assert len(topics) == len(set(topics))
    assert {Topic.KEY_RATE, Topic.FX, Topic.TRADE, Topic.SANCTIONS} <= set(topics)


def test_industry_by_name_is_reported() -> None:
    profile = assemble(
        CompanyRow(company_id="U_2", okved_name="Деятельность ресторанов"), CompanyLinks()
    )
    assert profile.industry.resolved_by == "name"
    assert any("по названию" in warning for warning in profile.data_quality.warnings)


class FakeConnection:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    async def fetchrow(self, *_: Any) -> None:
        if self.error:
            raise self.error


async def call(connection: Any, path: str = "/api/companies/U_1/profile") -> Any:
    app = create_app()

    async def override() -> AsyncIterator[Any]:
        yield connection

    app.dependency_overrides[get_connection] = override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(path)


async def test_unknown_company_is_404() -> None:
    response = await call(FakeConnection())
    assert response.status_code == 404
    assert response.json() == {"detail": "Компания не найдена"}


async def test_not_loaded_data_is_503() -> None:
    response = await call(FakeConnection(asyncpg.UndefinedTableError("нет таблицы")))
    assert response.status_code == 503
    assert "python -m app.data" in response.json()["detail"]


async def test_unavailable_database_is_503(monkeypatch: pytest.MonkeyPatch) -> None:
    async def broken_pool() -> None:
        raise OSError("connection refused")

    monkeypatch.setattr("app.api.companies.get_pool", broken_pool)
    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        response = await client.get("/api/companies/U_1/profile")
    assert response.status_code == 503
    assert response.json() == {"detail": "База данных недоступна"}


async def test_busy_pool_is_503_instead_of_hanging(monkeypatch: pytest.MonkeyPatch) -> None:
    class BusyPool:
        async def acquire(self, **options: float) -> None:
            assert options == {"timeout": 10}
            raise TimeoutError

    async def busy_pool() -> BusyPool:
        return BusyPool()

    monkeypatch.setattr("app.api.companies.get_pool", busy_pool)
    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        response = await client.get("/api/companies/U_1/profile")
    assert response.status_code == 503
    assert response.json() == {"detail": "Сервис занят другими запросами, повторите позже"}


async def test_connection_returns_to_pool_after_request(monkeypatch: pytest.MonkeyPatch) -> None:
    released: list[object] = []
    connection = FakeConnection(asyncpg.UndefinedTableError("нет таблицы"))

    class Pool:
        async def acquire(self, **options: float) -> FakeConnection:
            return connection

        async def release(self, value: object) -> None:
            released.append(value)

    async def pool() -> Pool:
        return Pool()

    monkeypatch.setattr("app.api.companies.get_pool", pool)
    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        response = await client.get("/api/companies/U_1/profile")
    assert response.status_code == 503
    assert released == [connection]


async def test_openapi_documents_profile_contract() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        schema = (await client.get("/api/openapi.json")).json()
    operation = schema["paths"]["/api/companies/{company_id}/profile"]["get"]
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/CompanyProfile"
    }
    assert {"404", "503"} <= set(operation["responses"])


async def test_profile_endpoint_on_loaded_data(
    database: asyncpg.Connection, tmp_path: Path
) -> None:
    from app.data.loader import read_case, store

    lines = [HEADER, company_line(SYNTHETIC_U="U_1", GROUP_ID="g_1")]
    lines += [
        company_line(SYNTHETIC_U=f"U_{i}", GROUP_ID="g_1", REVENUE_COMPANY=str(i * 10**9))
        for i in range(2, 13)
    ]
    companies = write(tmp_path / "c.csv", lines)
    links = write(tmp_path / "l.csv", ["SYNTHETIC_F;RC_NAME;SYNTHETIC_U", "F_1;МОСКОВСКИЙ;U_1"])
    await store(database, read_case(companies, links))

    response = await call(database)

    assert response.status_code == 200
    profile = response.json()
    assert profile["company_id"] == "U_1"
    assert profile["group"]["companies_count"] == 12
    assert profile["market_position"]["revenue_rank"] == 1
    assert profile["market_position"]["peer_count"] == 12
    features = {feature["id"]: feature for feature in profile["features"]}
    assert features["feature:revenue"]["source_fields"] == ["REVENUE_COMPANY"]
    assert features["feature:loan"]["source_fields"] == ["CL_PRODUCT_LOA_MARK"]
    assert features["feature:persons"]["value"] == 1
    assert "feature:market_rank" in features
