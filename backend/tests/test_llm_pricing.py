import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import Settings
from app.llm.catalog import PROVIDER_MODELS
from app.llm.client import LLMClient
from app.llm.errors import LLMConfigurationError
from app.llm.models import AllowedModel, Message, PricingInfo, Task
from app.llm.pricing import PUBLIC_USD_PER_MILLION, estimated_model
from tests.test_llm import RecordingBudget, completion


def estimate_settings(**overrides: object) -> Settings:
    return Settings(
        _env_file=None,
        llm_api_key=SecretStr("test-only-key"),
        llm_pricing_mode="public_estimate",
        llm_model_fast=AllowedModel.DEEPSEEK,
        llm_model_smart=AllowedModel.GLM,
        llm_models={},
        **overrides,
    )


def test_prices_match_saved_public_snapshot() -> None:
    snapshot = json.loads(
        (Path(__file__).resolve().parents[2] / "docs/llm-public-pricing.json").read_text()
    )
    by_id = {row["provider_id"]: row for row in snapshot["models"]}
    assert set(PUBLIC_USD_PER_MILLION) == set(AllowedModel)
    for name, (input_price, output_price) in PUBLIC_USD_PER_MILLION.items():
        saved = by_id[PROVIDER_MODELS[name].provider_id]
        assert input_price == Decimal(saved["input_usd_per_million"])
        assert output_price == Decimal(saved["output_usd_per_million"])


async def test_estimated_price_works_without_explicit_registry() -> None:
    settings = estimate_settings()
    budget = RecordingBudget()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=completion()))
    ) as http:
        client = LLMClient(settings, http, budget)
        result = await client.complete(Task.CLASSIFY, [Message(role="user", content="text")])
        assert client.select_model(Task.INTERPRET)[0] == AllowedModel.GLM
    assert result.model == AllowedModel.DEEPSEEK
    assert result.cost_rub == Decimal("0.001047")
    assert result.pricing.mode == "public_estimate"
    assert result.pricing.usd_rub == Decimal("100")
    assert result.pricing.safety_multiplier == Decimal("2")
    assert result.pricing.as_of.isoformat() == "2026-10-20"
    assert result.model_dump(mode="json")["pricing"]["mode"] == "public_estimate"
    assert budget.charges == [1047]


async def test_explicit_mode_has_no_estimate_fallback() -> None:
    settings = estimate_settings()
    settings.llm_pricing_mode = "explicit"
    async with httpx.AsyncClient() as http:
        with pytest.raises(LLMConfigurationError):
            LLMClient(settings, http, RecordingBudget()).select_model(Task.CLASSIFY)


def test_estimate_applies_configured_exchange_rate_and_margin() -> None:
    model = estimated_model(AllowedModel.GLM, Decimal("120"), Decimal("3"))
    assert model.input_rub_per_million == Decimal("17.280")
    assert model.output_rub_per_million == Decimal("43.20")


async def test_explicit_tariff_overrides_public_estimate() -> None:
    settings = estimate_settings()
    model = estimated_model(AllowedModel.DEEPSEEK, Decimal("100"), Decimal("2"))
    settings.llm_models = {
        AllowedModel.DEEPSEEK: model.model_copy(
            update={
                "input_rub_per_million": Decimal("1"),
                "output_rub_per_million": Decimal("2"),
                "pricing": PricingInfo(),
            }
        )
    }
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=completion()))
    ) as http:
        result = await LLMClient(settings, http, RecordingBudget()).complete(
            Task.CLASSIFY, [Message(role="user", content="text")]
        )
    assert result.cost_rub == Decimal("0.00014")
    assert result.pricing.mode == "configured"
    assert result.pricing.usd_rub is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"llm_estimate_usd_rub": "0"},
        {"llm_estimate_usd_rub": "NaN"},
        {"llm_estimate_safety_multiplier": "0.5"},
        {"llm_estimate_safety_multiplier": "Infinity"},
    ],
)
def test_invalid_estimate_configuration_is_rejected(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        estimate_settings(**overrides)
