from datetime import date
from decimal import Decimal

from app.llm.catalog import PROVIDER_MODELS
from app.llm.models import AllowedModel, ModelConfig, PricingInfo

PUBLIC_USD_PER_MILLION = {
    AllowedModel.GEMMA: (Decimal("0.0765"), Decimal("0.255")),
    AllowedModel.GLM: (Decimal("0.048"), Decimal("0.12")),
    AllowedModel.QWEN: (Decimal("0.10"), Decimal("0.95")),
    AllowedModel.DEEPSEEK: (Decimal("0.02832"), Decimal("0.12")),
    AllowedModel.QWEN_VL: (Decimal("0.15"), Decimal("0.60")),
    AllowedModel.KIMI: (Decimal("0.65"), Decimal("3.41")),
}


def estimated_model(
    name: AllowedModel, usd_rub: Decimal, safety_multiplier: Decimal
) -> ModelConfig:
    provider = PROVIDER_MODELS[name]
    input_usd, output_usd = PUBLIC_USD_PER_MILLION[name]
    return ModelConfig(
        provider_id=provider.provider_id,
        max_input_tokens=provider.max_input_tokens,
        input_rub_per_million=input_usd * usd_rub * safety_multiplier,
        output_rub_per_million=output_usd * usd_rub * safety_multiplier,
        pricing=PricingInfo(
            mode="public_estimate",
            source="https://openrouter.ai/api/v1/models",
            as_of=date(2026, 10, 20),
            usd_rub=usd_rub,
            safety_multiplier=safety_multiplier,
        ),
    )
