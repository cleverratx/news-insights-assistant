from fastapi import FastAPI

from app.core.config import get_settings

app = FastAPI(
    title="Новостная аналитика для принятия бизнес-решений",
    version="0.1.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)


@app.get("/api/health", tags=["service"])
async def health() -> dict[str, str]:
    """<summary>Проверка живости сервиса и выбранного окружения.</summary>"""
    settings = get_settings()
    return {"status": "ok", "env": settings.app_env}
