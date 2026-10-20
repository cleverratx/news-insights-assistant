import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from dotenv import dotenv_values

from app.core.config import Settings

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def compose() -> dict[str, object]:
    if not shutil.which("docker"):
        pytest.skip("Docker CLI не установлен")
    version = subprocess.run(["docker", "compose", "version"], capture_output=True, timeout=10)
    if version.returncode:
        pytest.skip("Docker Compose не установлен")
    environment = dict(os.environ)
    environment.update(
        {name: value or "" for name, value in dotenv_values(ROOT / ".env.example").items()}
    )
    environment.update(APP_PORT="19000", WEB_PORT="19080", LLM_API_KEY="test-only-compose-key")
    result = subprocess.run(
        ["docker", "compose", "--env-file", ".env.example", "config", "--format", "json"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        check=True,
        timeout=20,
    )
    return json.loads(result.stdout)


def test_env_template_and_compose_cover_application_settings(compose: dict[str, object]) -> None:
    expected = {name.upper() for name in Settings.model_fields}
    example = dotenv_values(ROOT / ".env.example")
    assert expected <= set(example)
    assert not example["LLM_API_KEY"]
    for name in ("api", "worker"):
        assert expected <= set(compose["services"][name]["environment"])


def test_port_overrides_reach_api_and_nginx(compose: dict[str, object]) -> None:
    services = compose["services"]
    assert services["api"]["environment"]["APP_PORT"] == "19000"
    assert services["worker"]["environment"]["APP_PORT"] == "19000"
    assert services["api"]["ports"][0]["target"] == 19000
    assert services["web"]["ports"][0]["published"] == "19080"
    assert services["web"]["environment"]["API_UPSTREAM"] == "api:19000"


def test_secrets_and_stores_are_isolated(compose: dict[str, object]) -> None:
    services = compose["services"]
    assert services["api"]["environment"]["LLM_API_KEY"] == "test-only-compose-key"
    assert services["worker"]["environment"]["LLM_API_KEY"] == "test-only-compose-key"
    assert "LLM_API_KEY" not in services["web"]["environment"]
    for name in ("api", "worker", "web"):
        assert not services[name]["build"].get("args")
    for name in ("postgres", "redis"):
        assert not services[name].get("ports")
        assert services[name]["volumes"][0]["type"] == "volume"
    for name in ("api", "worker"):
        assert services[name]["volumes"][0]["read_only"]
