import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings, get_settings
from app.main import create_app


@pytest.mark.parametrize("path", ["/health", "/api/health"])
async def test_health_returns_configured_environment(path: str) -> None:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, app_env="ci")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(path)

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "env": "ci"}


async def test_openapi_exposes_typed_health_response() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        response = await client.get("/api/openapi.json")

    assert response.status_code == 200
    schema = response.json()
    assert "/health" not in schema["paths"]
    health = schema["paths"]["/api/health"]["get"]
    assert health["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/HealthResponse"
    }


async def test_docs_available() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        assert (await client.get("/api/docs")).status_code == 200


async def test_health_without_llm_or_infrastructure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.setenv("REDIS_URL", "redis://unavailable.test:1/0")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://unavailable.test:1/test")
    get_settings.cache_clear()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=create_app()), base_url="http://test"
        ) as client:
            assert (await client.get("/health")).status_code == 200
    finally:
        get_settings.cache_clear()
