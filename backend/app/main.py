from fastapi import FastAPI

from app.api.health import router as health_router
from app.api.router import router as api_router
from app.core.config import get_settings


def create_app() -> FastAPI:
    get_settings()
    application = FastAPI(
        title="Новостная аналитика для принятия бизнес-решений",
        version="0.1.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    application.include_router(health_router, include_in_schema=False)
    application.include_router(api_router, prefix="/api")
    return application


app = create_app()
