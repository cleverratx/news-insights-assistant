from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings, get_settings


def test_environment_overrides_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("APP_ENV=stand\nAPP_PORT=9000\nPOSTGRES_HOST=postgres\n")
    monkeypatch.setenv("APP_ENV", "ci")
    monkeypatch.delenv("APP_PORT", raising=False)

    settings = Settings(_env_file=env_file)

    assert settings.app_env == "ci"
    assert settings.app_port == 9000


def test_default_dotenv_path_is_independent_of_working_directory() -> None:
    assert Settings.model_config["env_file"] == Path(__file__).resolve().parents[2] / ".env"


@pytest.mark.parametrize(
    ("name", "value"),
    [("APP_ENV", "invalid"), ("APP_PORT", "0"), ("APP_PORT", "65536"), ("LOG_LEVEL", "invalid")],
)
def test_invalid_configuration_is_rejected(
    name: str, value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_settings_are_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("APP_ENV", "ci")
    try:
        assert get_settings() is get_settings()
        assert get_settings().app_env == "ci"
    finally:
        get_settings.cache_clear()
