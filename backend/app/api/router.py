from fastapi import APIRouter

from app.api.companies import router as companies_router
from app.api.health import router as health_router

router = APIRouter()
router.include_router(health_router)
router.include_router(companies_router)
