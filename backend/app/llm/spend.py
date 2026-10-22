from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from decimal import Decimal

import structlog
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.llm.budget import UNITS_PER_RUBLE

logger = structlog.get_logger(__name__)


@dataclass
class RequestSpend:
    calls: int = 0
    units: int = 0
    models: list[str] = field(default_factory=list)

    @property
    def cost_rub(self) -> Decimal:
        return Decimal(self.units) / UNITS_PER_RUBLE


current: ContextVar[RequestSpend | None] = ContextVar("llm_request_spend", default=None)


@contextmanager
def spend_scope() -> Iterator[RequestSpend]:
    spend = RequestSpend()
    token = current.set(spend)
    try:
        yield spend
    finally:
        current.reset(token)


def record(model: str, units: int) -> None:
    spend = current.get()
    if spend is None:
        return
    spend.calls += 1
    spend.units += units
    if model not in spend.models:
        spend.models.append(model)


class LLMSpendMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        with spend_scope() as spend:

            async def tracked(message: Message) -> None:
                if message["type"] == "http.response.start":
                    headers = MutableHeaders(scope=message)
                    headers["X-LLM-Calls"] = str(spend.calls)
                    headers["X-LLM-Cost-Rub"] = str(spend.cost_rub)
                await send(message)

            try:
                await self.app(scope, receive, tracked)
            finally:
                if spend.calls:
                    logger.info(
                        "Расход LLM на запрос",
                        path=scope.get("path"),
                        calls=spend.calls,
                        cost_rub=str(spend.cost_rub),
                        models=spend.models,
                    )
