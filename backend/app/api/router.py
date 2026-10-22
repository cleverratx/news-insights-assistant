from fastapi import APIRouter

from app.api.companies import router as companies_router
from app.api.competitors import router as competitors_router
from app.api.digest import router as digest_router
from app.api.feed import router as feed_router
from app.api.health import router as health_router
from app.api.insights import router as insights_router
from app.api.knowledge import router as knowledge_router
from app.api.links import router as links_router
from app.api.llm import router as llm_router
from app.api.news_links import router as news_links_router
from app.api.related_feed import router as related_feed_router

router = APIRouter()
router.include_router(health_router)
router.include_router(companies_router)
router.include_router(knowledge_router)
router.include_router(links_router)
router.include_router(competitors_router)
router.include_router(feed_router)

router.include_router(related_feed_router)

router.include_router(insights_router)
router.include_router(llm_router)
router.include_router(news_links_router)
router.include_router(digest_router)
