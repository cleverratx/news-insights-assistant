from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.llm.models import AllowedModel, ModelConfig


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[3] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    app_env: Literal["local", "ci", "stand"] = "local"
    app_port: int = Field(default=8000, ge=1, le=65535)
    log_level: Literal["debug", "info", "warning", "error", "critical"] = "info"

    database_url: SecretStr = SecretStr("postgresql+asyncpg://alfa:change-me@localhost:5432/alfa")
    redis_url: SecretStr = SecretStr("redis://localhost:6379/0")

    llm_base_url: str = "https://changellenge.up.railway.app/v1"
    llm_api_key: SecretStr = SecretStr("")
    llm_model_fast: AllowedModel | None = AllowedModel.DEEPSEEK
    llm_model_smart: AllowedModel | None = AllowedModel.GLM
    llm_models: dict[AllowedModel, ModelConfig] = Field(default_factory=dict)
    llm_pricing_mode: Literal["explicit", "public_estimate"] = "public_estimate"
    llm_estimate_usd_rub: Decimal = Field(
        default=Decimal("100"), gt=0, le=10000, allow_inf_nan=False
    )
    llm_estimate_safety_multiplier: Decimal = Field(
        default=Decimal("2"), ge=1, le=100, allow_inf_nan=False
    )
    llm_daily_budget_rub: Decimal = Field(
        default=Decimal("500"), ge=Decimal("0.000001"), le=500, allow_inf_nan=False
    )
    llm_request_timeout_seconds: int = Field(default=60, gt=0, le=3600)
    llm_max_retries: int = Field(default=2, ge=0, le=5)
    llm_retry_base_seconds: float = Field(default=0.5, ge=0, le=10, allow_inf_nan=False)
    llm_max_output_tokens: int = Field(default=1024, gt=0, le=32768)
    llm_max_input_bytes: int = Field(default=24000, gt=0, le=1000000)
    llm_budget_timezone: str = "UTC"

    news_sources_file: str = "app/news/sources.yaml"
    news_fetch_interval_minutes: int = Field(default=30, gt=0)
    news_user_agent: str = "alfa-news-insights/0.1"

    data_dir: str = "data"
    legend_file: str = "samples/legends.yaml"

    @field_validator("llm_model_fast", "llm_model_smart", mode="before")
    @classmethod
    def empty_model_is_unconfigured(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("llm_budget_timezone")
    @classmethod
    def valid_budget_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("Неизвестный часовой пояс бюджета LLM") from None
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
