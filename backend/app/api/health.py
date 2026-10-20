from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.config import Settings, get_settings

router = APIRouter(tags=["service"])


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    env: Literal["local", "ci", "stand"]


@router.get("/health", response_model=HealthResponse)
async def health(settings: Annotated[Settings, Depends(get_settings)]) -> HealthResponse:
    """Проверка живости API без обращения к внешним сервисам."""
    return HealthResponse(env=settings.app_env)
