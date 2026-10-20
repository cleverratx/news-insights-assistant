from unittest.mock import patch

import pytest

from app.__main__ import main
from app.core.config import get_settings


def test_entrypoint_uses_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_PORT", "9000")
    monkeypatch.setenv("LOG_LEVEL", "warning")
    get_settings.cache_clear()
    try:
        with patch("app.__main__.uvicorn.run") as run:
            main()
        run.assert_called_once_with("app.main:app", host="0.0.0.0", port=9000, log_level="warning")
    finally:
        get_settings.cache_clear()
