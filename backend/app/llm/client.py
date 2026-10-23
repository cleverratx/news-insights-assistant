import asyncio
import json
import math
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal
from secrets import randbelow
from time import monotonic
from zoneinfo import ZoneInfo

import httpx
import structlog
from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.llm.budget import UNITS_PER_RUBLE, Budget
from app.llm.catalog import PROVIDER_MODELS
from app.llm.errors import (
    BudgetExceededError,
    LLMConfigurationError,
    LLMProtocolError,
    LLMProviderError,
)
from app.llm.models import (
    AllowedModel,
    Completion,
    LLMResult,
    Message,
    ModelConfig,
    Task,
    Usage,
    cost_units,
)
from app.llm.pricing import estimated_model
from app.llm.spend import record
from app.llm.usage import open_budget

logger = structlog.get_logger(__name__)
FAST_TASKS = frozenset({Task.CLASSIFY, Task.EXTRACT, Task.SUMMARIZE})
PROMPT_OVERHEAD_TOKENS = 512
MESSAGE_OVERHEAD_TOKENS = 16


def price(model: ModelConfig) -> Decimal:
    return model.input_rub_per_million + model.output_rub_per_million


def prompt_ceiling(input_bytes: int, messages: int, model: ModelConfig) -> int:
    """Потолок входных токенов по размеру запроса: токен не короче байта, служебная разметка
    чата — сверху. Резерв по окну модели (до 1 млн токенов) списывался целиком на каждом
    таймауте и за день выбирал дневной лимит при копеечном фактическом расходе."""
    overhead = PROMPT_OVERHEAD_TOKENS + MESSAGE_OVERHEAD_TOKENS * messages
    return min(model.max_input_tokens, input_bytes + overhead)


class LLMClient:
    def __init__(self, settings: Settings, http: httpx.AsyncClient, budget: Budget) -> None:
        self.settings = settings
        self.http = http
        self.budget = budget
        try:
            url = httpx.URL(settings.llm_base_url)
        except httpx.InvalidURL:
            raise LLMConfigurationError("Некорректный адрес API LLM") from None
        if (
            url.scheme not in {"http", "https"}
            or (url.scheme == "http" and url.host not in {"localhost", "127.0.0.1", "::1"})
            or not url.host
            or url.userinfo
            or url.query
            or url.fragment
            or not settings.llm_api_key.get_secret_value()
        ):
            raise LLMConfigurationError("Укажите адрес API и ключ LLM в окружении")
        self.endpoint = str(url).rstrip("/") + "/chat/completions"

    def select_model(self, task: Task, economy: bool = False) -> tuple[AllowedModel, ModelConfig]:
        if not isinstance(task, Task):
            raise LLMConfigurationError("Неизвестный тип задачи LLM")
        fast = self.settings.llm_model_fast
        default = fast if task in FAST_TASKS else self.settings.llm_model_smart
        name = self.settings.llm_routes.get(task, default)
        if name is None:
            raise LLMConfigurationError("Для задачи не настроены разрешённая модель и тарифы")
        chosen = name, self.model_config(name)
        if economy and task not in FAST_TASKS and fast is not None and fast != name:
            cheaper = fast, self.model_config(fast)
            if price(cheaper[1]) < price(chosen[1]):
                return cheaper
        return chosen

    def model_config(self, name: AllowedModel) -> ModelConfig:
        model = self.settings.llm_models.get(name)
        if model is None and self.settings.llm_pricing_mode == "public_estimate":
            model = estimated_model(
                name,
                self.settings.llm_estimate_usd_rub,
                self.settings.llm_estimate_safety_multiplier,
            )
        if model is None:
            raise LLMConfigurationError("Для задачи не настроены разрешённая модель и тарифы")
        expected = PROVIDER_MODELS[name]
        if (
            model.provider_id != expected.provider_id
            or model.max_input_tokens != expected.max_input_tokens
        ):
            raise LLMConfigurationError("ID или лимит модели не соответствует каталогу провайдера")
        return model

    def budget_day(self) -> str:
        return datetime.now(ZoneInfo(self.settings.llm_budget_timezone)).date().isoformat()

    def daily_limit(self) -> int:
        return int(self.settings.llm_daily_budget_rub * UNITS_PER_RUBLE)

    def task_limit(self, task: Task) -> int:
        limit = self.daily_limit()
        if task in FAST_TASKS:
            return max(1, int(limit * self.settings.llm_bulk_budget_share))
        return limit

    async def economy(self, task: Task, day: str) -> bool:
        share = self.settings.llm_economy_share
        if task in FAST_TASKS or share >= 1:
            return False
        return await self.budget.spent(day) >= self.daily_limit() * share

    async def complete(self, task: Task, messages: Sequence[Message]) -> LLMResult:
        self.select_model(task)
        if not messages:
            raise LLMConfigurationError("Список сообщений LLM пуст")
        payload = [message.model_dump() for message in messages]
        input_bytes = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        if input_bytes > self.settings.llm_max_input_bytes:
            raise LLMConfigurationError("Превышен лимит размера входа LLM")
        economy = await self.economy(task, self.budget_day())
        name, model = self.select_model(task, economy)
        reservation = cost_units(
            Usage(
                prompt_tokens=prompt_ceiling(input_bytes, len(payload), model),
                completion_tokens=self.settings.llm_max_output_tokens,
            ),
            model,
        )
        for attempt in range(self.settings.llm_max_retries + 1):
            try:
                return await self._attempt(task, name, model, payload, reservation, economy)
            except LLMProviderError as error:
                if not error.retryable or attempt == self.settings.llm_max_retries:
                    raise
                delay = self.settings.llm_retry_base_seconds * 2**attempt
                jitter = delay * randbelow(1000) / 1000
                await asyncio.sleep(min(30, max(delay + jitter, error.retry_after)))
        raise AssertionError("Недостижимое состояние повторов LLM")

    async def _attempt(
        self,
        task: Task,
        name: AllowedModel,
        model: ModelConfig,
        messages: list[dict[str, object]],
        amount: int,
        economy: bool,
    ) -> LLMResult:
        day = self.budget_day()
        label = f"{task.value}:{name.value}"
        reservation = await self.budget.reserve(day, amount, self.task_limit(task), label)
        charge = amount
        usage: Usage | None = None
        status = "неопределённый расход"
        started = monotonic()
        authorization = f"Bearer {self.settings.llm_api_key.get_secret_value()}"
        body: dict[str, object] = {
            "model": model.provider_id,
            "messages": messages,
            "max_tokens": self.settings.llm_max_output_tokens,
            "stream": False,
        }
        if task not in self.settings.llm_reasoning_tasks:
            body["reasoning"] = {"enabled": False}
        try:
            try:
                async with asyncio.timeout(self.settings.llm_request_timeout_seconds):
                    response = await self.http.post(
                        self.endpoint,
                        headers={"Authorization": authorization},
                        json=body,
                        timeout=self.settings.llm_request_timeout_seconds,
                        follow_redirects=False,
                    )
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
                charge = 0
                status = "соединение не установлено"
                raise LLMProviderError(retryable=True) from None
            except (httpx.TimeoutException, TimeoutError):
                status = "ответ не дождались: списан потолок резерва"
                raise LLMProviderError(retryable=False) from None
            except httpx.HTTPError:
                raise LLMProviderError(retryable=True) from None
            if response.is_error or response.is_redirect:
                if self._provider_budget_exceeded(response):
                    charge = 0
                    status = "бюджет провайдера исчерпан"
                    raise BudgetExceededError("Исчерпан дневной бюджет ключа провайдера")
                if response.status_code in {400, 401, 403, 404, 405, 413, 422, 429}:
                    charge = 0
                    status = "запрос отклонён"
                raise LLMProviderError(
                    retryable=response.status_code in {408, 429, 500, 502, 503, 504},
                    retry_after=self._retry_after(response),
                )
            try:
                data = response.json()
                if not isinstance(data, dict):
                    raise LLMProtocolError("Некорректный формат ответа LLM")
                usage = Usage.model_validate(data.get("usage"))
                charge = cost_units(usage, model)
                status = "учтено по токенам"
                completion = Completion.model_validate(data)
            except (ValueError, ValidationError):
                raise LLMProtocolError(
                    "Ответ LLM не содержит корректного текста или usage"
                ) from None
            choice = completion.choices[0]
            if choice.finish_reason != "stop":
                raise LLMProtocolError("LLM вернула незавершённый ответ")
            if usage.completion_tokens > self.settings.llm_max_output_tokens:
                raise LLMProtocolError("Провайдер превысил лимит выходных токенов")
            if usage.prompt_tokens > model.max_input_tokens:
                raise LLMProtocolError("Провайдер превысил настроенный лимит входных токенов")
            return LLMResult(
                text=choice.message.content,
                model=name,
                usage=usage,
                cost_rub=Decimal(charge) / UNITS_PER_RUBLE,
                pricing=model.pricing,
            )
        finally:
            await asyncio.shield(self.budget.settle(reservation, charge))
            record(name.value, charge)
            logger.info(
                "Учтена попытка обращения к LLM",
                task=task.value,
                model=name.value,
                economy=economy,
                request_id=reservation.token,
                budget_day=day,
                input_tokens=usage.prompt_tokens if usage else None,
                output_tokens=usage.completion_tokens if usage else None,
                cost_rub=str(Decimal(charge) / UNITS_PER_RUBLE),
                accounting=status,
                pricing_mode=model.pricing.mode,
                pricing_source=model.pricing.source,
                pricing_as_of=str(model.pricing.as_of) if model.pricing.as_of else None,
                estimate_usd_rub=str(model.pricing.usd_rub),
                estimate_safety_multiplier=str(model.pricing.safety_multiplier),
                duration_ms=round((monotonic() - started) * 1000),
            )

    @staticmethod
    def _provider_budget_exceeded(response: httpx.Response) -> bool:
        try:
            data = response.json()
        except ValueError:
            return False
        if not isinstance(data, dict):
            return False
        error = data.get("error")
        message = error.get("message") if isinstance(error, dict) else error
        return isinstance(message, str) and "budget has been exceeded" in message.casefold()

    @staticmethod
    def _retry_after(response: httpx.Response) -> float:
        try:
            delay = float(response.headers.get("Retry-After", "0"))
        except ValueError:
            return 0
        return max(0, min(30, delay)) if math.isfinite(delay) else 0


@asynccontextmanager
async def open_llm_client(settings: Settings | None = None) -> AsyncIterator[LLMClient]:
    config = settings or get_settings()
    async with open_budget(config) as budget, httpx.AsyncClient() as http:
        yield LLMClient(config, http, budget)
