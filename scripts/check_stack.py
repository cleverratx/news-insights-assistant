import argparse
import json
import re
import shutil
import subprocess
from http.client import HTTPConnection, HTTPSConnection
from pathlib import Path
from urllib.parse import urljoin, urlsplit

BACKEND_PROBE = """
import asyncio
import asyncpg
from arq import create_pool
from app.core.config import get_settings
from app.worker.settings import redis_settings_from_url

async def main():
    settings = get_settings()
    dsn = settings.database_url.get_secret_value().replace(
        'postgresql+asyncpg://', 'postgresql://', 1
    )
    connection = await asyncpg.connect(dsn, timeout=10)
    try:
        assert await connection.fetchval('SELECT 1') == 1
    finally:
        await connection.close()
    redis = await create_pool(redis_settings_from_url(settings.redis_url.get_secret_value()))
    try:
        job = await redis.enqueue_job('ping')
        assert job is not None
        assert await job.result(timeout=15) == 'pong'
    finally:
        await redis.aclose()
    print('PostgreSQL и очередь worker работают')

asyncio.run(main())
"""


def fetch(base: str, path: str) -> bytes:
    url = urlsplit(urljoin(base.rstrip("/") + "/", path))
    if url.scheme not in {"http", "https"} or not url.hostname or url.username:
        raise ValueError("Для проверки нужен HTTP-адрес без учётных данных")
    connection_type = HTTPSConnection if url.scheme == "https" else HTTPConnection
    connection = connection_type(url.hostname, url.port, timeout=10)
    try:
        target = url.path or "/"
        if url.query:
            target += "?" + url.query
        connection.request("GET", target)
        response = connection.getresponse()
        if response.status != 200:
            raise RuntimeError("HTTP-проверка завершилась ошибкой")
        return response.read()
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Проверка Docker-стека без вызовов LLM")
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--web-url", default="http://127.0.0.1:8080")
    args = parser.parse_args()
    for base, path in [(args.api_url, "/health"), (args.web_url, "/api/health")]:
        if json.loads(fetch(base, path))["status"] != "ok":
            raise RuntimeError("API не подтвердил готовность")
    schema = json.loads(fetch(args.web_url, "/api/openapi.json"))
    if "/api/health" not in schema["paths"]:
        raise RuntimeError("Ошибка схемы OpenAPI")
    fetch(args.web_url, "/api/docs")
    index = fetch(args.web_url, "/")
    asset = re.search(rb'<script[^>]+src="([^"]+)"', index)
    if asset is None:
        raise RuntimeError("Сборка фронтенда недоступна")
    asset_body = fetch(args.web_url, asset[1].decode())
    if not asset_body or asset_body == index:
        raise RuntimeError("Сборка фронтенда недоступна")
    if fetch(args.web_url, "/roles/smoke-test") != index:
        raise RuntimeError("Ошибка маршрутизации SPA")
    docker = shutil.which("docker")
    if docker is None:
        raise RuntimeError("Docker CLI не установлен")
    subprocess.run(  # noqa: S603
        [docker, "compose", "exec", "-T", "api", "python", "-"],
        input=BACKEND_PROBE,
        text=True,
        cwd=Path(__file__).resolve().parents[1],
        check=True,
        timeout=40,
    )
    print("API, nginx, OpenAPI, Swagger, фронтенд и маршрутизация SPA работают")


if __name__ == "__main__":
    main()
