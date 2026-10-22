from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from redis.exceptions import RedisError

from app.core.config import Settings, get_settings
from app.llm.errors import BudgetUnavailableError
from app.llm.usage import UsageReport, open_budget, report

router = APIRouter(prefix="/llm", tags=["llm"])


@router.get(
    "/usage",
    response_model=UsageReport,
    responses={503: {"description": "Хранилище бюджета недоступно"}},
)
async def llm_usage(settings: Annotated[Settings, Depends(get_settings)]) -> UsageReport:
    """Расход LLM за текущие бюджетные сутки: лимит, потрачено, резерв, режим и разбивка по задачам
    и моделям. Модель не вызывается."""
    try:
        async with open_budget(settings) as budget:
            return await report(settings, budget)
    except (BudgetUnavailableError, RedisError, OSError):
        raise HTTPException(503, "Хранилище бюджета LLM недоступно") from None
