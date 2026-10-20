import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import patch

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import Settings
from app.llm.budget import Reservation
from app.llm.client import LLMClient
from app.llm.errors import (
    BudgetExceededError,
    BudgetUnavailableError,
    LLMConfigurationError,
    LLMProtocolError,
    LLMProviderError,
)
from app.llm.models import AllowedModel, LLMResult, Message, ModelConfig, Task, Usage, cost_units


class RecordingBudget:
    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed
        self.reservations: list[Reservation] = []
        self.charges: list[int] = []

    async def reserve(self, day: str, amount: int, limit: int) -> Reservation:
        if not self.allowed or amount > limit:
            raise BudgetExceededError("Бюджет исчерпан")
        reservation = Reservation(day, str(len(self.reservations)), amount)
        self.reservations.append(reservation)
        return reservation

    async def settle(self, reservation: Reservation, actual: int) -> None:
        self.charges.append(actual)


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        llm_base_url="https://llm.test/v1",
        llm_api_key=SecretStr("test-only-secret-key"),
        llm_model_fast=AllowedModel.GEMMA,
        llm_model_smart=AllowedModel.GLM,
        llm_models={
            AllowedModel.GEMMA: ModelConfig(
                provider_id="google/gemma-4-26b-a4b-it",
                max_input_tokens=262144,
                input_rub_per_million=Decimal("10"),
                output_rub_per_million=Decimal("20"),
            ),
            AllowedModel.GLM: ModelConfig(
                provider_id="z-ai/glm-5.3-flash",
                max_input_tokens=1048576,
                input_rub_per_million=Decimal("30"),
                output_rub_per_million=Decimal("60"),
            ),
        },
        llm_max_output_tokens=100,
        llm_retry_base_seconds=0,
        llm_budget_timezone="Europe/Moscow",
        llm_pricing_mode="explicit",
    )


def completion(**updates: object) -> dict[str, object]:
    return {
        "choices": [{"message": {"content": "private result"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20},
        **updates,
    }


async def run(
    settings: Settings,
    handler: Callable[[httpx.Request], httpx.Response],
    budget: RecordingBudget,
    task: Task = Task.CLASSIFY,
) -> LLMResult:
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = LLMClient(settings, http, budget)
        return await client.complete(task, [Message(role="user", content="private prompt")])


@pytest.mark.parametrize("task", list(Task))
async def test_routing_and_request_contract(settings: Settings, task: Task) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=completion())

    budget = RecordingBudget()
    await run(settings, handler, budget, task)

    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == "https://llm.test/v1/chat/completions"
    assert request.headers["Authorization"] == "Bearer test-only-secret-key"
    body = json.loads(request.content)
    expected_model = (
        "google/gemma-4-26b-a4b-it"
        if task in {Task.CLASSIFY, Task.EXTRACT, Task.SUMMARIZE}
        else "z-ai/glm-5.3-flash"
    )
    assert body["model"] == expected_model
    assert body["max_tokens"] == 100
    assert body["stream"] is False
    assert body["messages"] == [{"role": "user", "content": "private prompt"}]
    assert request.extensions["timeout"]["read"] == 60
    assert len(budget.reservations) == 1
    assert budget.charges[0] < budget.reservations[0].amount


async def test_cost_and_metadata_without_sensitive_content(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    budget = RecordingBudget()
    result = await run(settings, lambda _: httpx.Response(200, json=completion()), budget)
    assert result.cost_rub == Decimal("0.0014")
    assert result.usage.prompt_tokens == 100
    assert result.usage.completion_tokens == 20
    assert result.text == "private result"
    assert budget.charges == [1400]
    output = capsys.readouterr().out
    for secret in ("private prompt", "private result", "test-only-secret-key"):
        assert secret not in output
    assert "input_tokens=100" in output


async def test_retries_are_budgeted_separately(settings: Settings) -> None:
    statuses = iter([429, 503, 200])
    budget = RecordingBudget()
    await run(
        settings,
        lambda _: httpx.Response(next(statuses), json=completion(), headers={"Retry-After": "0"}),
        budget,
    )
    assert len(budget.reservations) == 3
    assert budget.charges == [0, budget.reservations[1].amount, 1400]


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
async def test_permanent_errors_are_not_retried(settings: Settings, status: int) -> None:
    budget = RecordingBudget()
    with pytest.raises(LLMProviderError):
        await run(settings, lambda _: httpx.Response(status, text="test-only-secret-key"), budget)
    assert len(budget.reservations) == 1
    assert budget.charges == [0]


@pytest.mark.parametrize("status", [307, 308])
async def test_redirects_are_not_followed(settings: Settings, status: int) -> None:
    budget = RecordingBudget()
    with pytest.raises(LLMProviderError):
        await run(
            settings,
            lambda _: httpx.Response(status, headers={"Location": "https://elsewhere.test"}),
            budget,
        )
    assert len(budget.reservations) == 1


async def test_budget_blocks_http_call(settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail("Бюджет должен проверяться до HTTP-запроса")

    with pytest.raises(BudgetExceededError):
        await run(settings, handler, RecordingBudget(allowed=False))


@pytest.mark.parametrize("failure", [httpx.ReadTimeout, httpx.ConnectError])
async def test_transport_failure_has_bounded_retries_and_safe_error(
    settings: Settings, failure: type[httpx.RequestError]
) -> None:
    budget = RecordingBudget()

    def handler(request: httpx.Request) -> httpx.Response:
        raise failure("test-only-secret-key", request=request)

    with pytest.raises(LLMProviderError) as error:
        await run(settings, handler, budget)
    assert "test-only-secret-key" not in str(error.value)
    assert error.value.__suppress_context__
    assert len(budget.reservations) == 3
    expected = budget.reservations[0].amount if failure is httpx.ReadTimeout else 0
    assert budget.charges == [expected] * 3


@pytest.mark.parametrize("body", [{}, {"usage": {"prompt_tokens": -1, "completion_tokens": 1}}])
async def test_unknown_usage_keeps_full_reservation(settings: Settings, body: object) -> None:
    budget = RecordingBudget()
    with pytest.raises(LLMProtocolError):
        await run(settings, lambda _: httpx.Response(200, json=body), budget)
    assert budget.charges == [budget.reservations[0].amount]


async def test_incomplete_response_is_charged_but_not_returned(settings: Settings) -> None:
    budget = RecordingBudget()
    body = completion(choices=[{"message": {"content": "partial"}, "finish_reason": "length"}])
    with pytest.raises(LLMProtocolError):
        await run(settings, lambda _: httpx.Response(200, json=body), budget)
    assert budget.charges == [1400]


async def test_unconfigured_model_prevents_request(settings: Settings) -> None:
    settings.llm_models = {}
    with pytest.raises(LLMConfigurationError):
        await run(
            settings, lambda _: pytest.fail("Запрос не должен выполняться"), RecordingBudget()
        )


async def test_input_limit_prevents_request(settings: Settings) -> None:
    settings.llm_max_input_bytes = 1
    with pytest.raises(LLMConfigurationError):
        await run(
            settings, lambda _: pytest.fail("Запрос не должен выполняться"), RecordingBudget()
        )


async def test_cancellation_keeps_worst_case_charge(settings: Settings) -> None:
    budget = RecordingBudget()

    def handler(request: httpx.Request) -> httpx.Response:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await run(settings, handler, budget)
    assert budget.charges == [budget.reservations[0].amount]


async def test_budget_outage_prevents_request(settings: Settings) -> None:
    class UnavailableBudget(RecordingBudget):
        async def reserve(self, day: str, amount: int, limit: int) -> Reservation:
            raise BudgetUnavailableError("Redis недоступен")

    with pytest.raises(BudgetUnavailableError):
        await run(
            settings, lambda _: pytest.fail("Запрос не должен выполняться"), UnavailableBudget()
        )


async def test_unapproved_provider_alias_prevents_request(settings: Settings) -> None:
    settings.llm_models[AllowedModel.GEMMA] = settings.llm_models[AllowedModel.GEMMA].model_copy(
        update={"provider_id": "deepseek-cost-test"}
    )
    with pytest.raises(LLMConfigurationError):
        await run(settings, lambda _: pytest.fail("Запрос запрещён"), RecordingBudget())


@pytest.mark.parametrize("status", [403, 429, 500])
async def test_provider_budget_error_is_not_retried(settings: Settings, status: int) -> None:
    budget = RecordingBudget()
    with pytest.raises(BudgetExceededError):
        await run(
            settings,
            lambda _: httpx.Response(
                status, json={"error": {"message": "Budget has been exceeded"}}
            ),
            budget,
        )
    assert len(budget.reservations) == 1
    assert budget.charges == [0]


async def test_budget_day_changes_at_three_moscow_time(settings: Settings) -> None:
    assert Settings(_env_file=None).llm_budget_timezone == "UTC"
    settings.llm_budget_timezone = "UTC"
    budget = RecordingBudget()
    with patch("app.llm.client.datetime") as clock:
        clock.now.return_value = datetime(2026, 10, 20, 23, 59, tzinfo=UTC)
        await run(settings, lambda _: httpx.Response(200, json=completion()), budget)
        clock.now.return_value = datetime(2026, 10, 21, 0, 0, tzinfo=UTC)
        await run(settings, lambda _: httpx.Response(200, json=completion()), budget)
    assert [item.day for item in budget.reservations] == ["2026-10-20", "2026-10-21"]


def test_rounding_is_conservative(settings: Settings) -> None:
    model = settings.llm_models[AllowedModel.GEMMA].model_copy(
        update={"input_rub_per_million": Decimal("0.01")}
    )
    assert cost_units(Usage(prompt_tokens=1, completion_tokens=0), model) == 1


async def test_whole_attempt_timeout(settings: Settings) -> None:
    settings.llm_request_timeout_seconds = 1
    settings.llm_max_retries = 0
    budget = RecordingBudget()

    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.Event().wait()
        raise AssertionError("Недостижимый ответ")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = LLMClient(settings, http, budget)
        with pytest.raises(LLMProviderError):
            await client.complete(Task.CLASSIFY, [Message(role="user", content="text")])
    assert budget.charges == [budget.reservations[0].amount]


async def test_invalid_json_keeps_reservation(settings: Settings) -> None:
    budget = RecordingBudget()
    with pytest.raises(LLMProtocolError):
        await run(settings, lambda _: httpx.Response(200, content=b"not-json"), budget)
    assert budget.charges == [budget.reservations[0].amount]


@pytest.mark.parametrize(
    "url", ["", "ftp://llm.test", "https://user:secret@llm.test", "https://llm.test?key=secret"]
)
async def test_unsafe_endpoint_is_rejected(settings: Settings, url: str) -> None:
    settings.llm_base_url = url
    with pytest.raises(LLMConfigurationError):
        await run(
            settings, lambda _: pytest.fail("Запрос не должен выполняться"), RecordingBudget()
        )


@pytest.mark.parametrize("changes", [{"llm_model_fast": "unapproved"}, {"llm_models": {"bad": {}}}])
def test_unapproved_model_is_rejected(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **changes)


@pytest.mark.parametrize("value", ["501", "NaN", "Infinity"])
def test_budget_cannot_exceed_case_limit(value: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, llm_daily_budget_rub=value)


def test_model_registry_from_environment(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LLM_MODEL_FAST", "gemma-4-26b-a4b")
    monkeypatch.setenv(
        "LLM_MODELS",
        json.dumps(
            {
                name.value: model.model_dump(mode="json")
                for name, model in settings.llm_models.items()
            }
        ),
    )
    parsed = Settings(_env_file=None)
    assert parsed.llm_model_fast == AllowedModel.GEMMA
    assert parsed.llm_models[AllowedModel.GEMMA].input_rub_per_million == Decimal("10")


@pytest.mark.parametrize("rate", [None, "-1", "NaN", "Infinity"])
def test_missing_or_invalid_tariff_is_rejected(rate: object) -> None:
    with pytest.raises(ValidationError):
        ModelConfig(
            provider_id="google/gemma-4-26b-a4b-it",
            max_input_tokens=262144,
            input_rub_per_million=rate,
            output_rub_per_million=Decimal("1"),
        )
