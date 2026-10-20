from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    llm_base_url: str = ""
    llm_api_key: SecretStr = SecretStr("")
    llm_model_fast: str = ""
    llm_model_smart: str = ""
    llm_daily_budget_rub: float = Field(default=500.0, gt=0)
    llm_request_timeout_seconds: int = Field(default=60, gt=0)

    news_sources_file: str = "config/sources.yaml"
    news_fetch_interval_minutes: int = Field(default=30, gt=0)
    news_user_agent: str = "alfa-news-insights/0.1"

    data_dir: str = "data"


@lru_cache
def get_settings() -> Settings:
    return Settings()
