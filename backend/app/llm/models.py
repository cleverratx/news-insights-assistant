from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AllowedModel(StrEnum):
    GEMMA = "gemma-4-26b-a4b"
    GLM = "glm-5.3-flash"
    QWEN = "qwen3.6-35b-a3b"
    DEEPSEEK = "deepseek-v4.1-flash"
    QWEN_VL = "qwen3-vl-30b-a3b-instruct"
    KIMI = "kimi-k2.6"


class Task(StrEnum):
    CLASSIFY = "classify"
    EXTRACT = "extract"
    SUMMARIZE = "summarize"
    RERANK = "rerank"
    INTERPRET = "interpret"
    RESEARCH = "research"


class PricingInfo(BaseModel):
    mode: Literal["configured", "public_estimate"] = "configured"
    source: str = "LLM_MODELS"
    as_of: date | None = None
    usd_rub: Decimal | None = None
    safety_multiplier: Decimal | None = None


class ModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    provider_id: str = Field(min_length=1)
    max_input_tokens: int = Field(gt=0, le=2_000_000)
    input_rub_per_million: Decimal = Field(ge=0, allow_inf_nan=False)
    output_rub_per_million: Decimal = Field(ge=0, allow_inf_nan=False)
    pricing: PricingInfo = Field(default_factory=PricingInfo)


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, repr=False)


class Usage(BaseModel):
    prompt_tokens: int = Field(ge=0, le=2_000_000, strict=True)
    completion_tokens: int = Field(ge=0, le=2_000_000, strict=True)


class LLMResult(BaseModel):
    text: str = Field(repr=False)
    model: AllowedModel
    usage: Usage
    cost_rub: Decimal
    pricing: PricingInfo


class CompletionMessage(BaseModel):
    content: str = Field(min_length=1, repr=False)


class Choice(BaseModel):
    message: CompletionMessage
    finish_reason: str


class Completion(BaseModel):
    choices: list[Choice] = Field(min_length=1)


def cost_units(usage: Usage, model: ModelConfig) -> int:
    cost = (
        usage.prompt_tokens * model.input_rub_per_million
        + usage.completion_tokens * model.output_rub_per_million
    )
    return int(cost.to_integral_value(rounding="ROUND_CEILING"))
