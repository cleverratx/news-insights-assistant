import argparse
import asyncio
import json
import logging
import sys
from dataclasses import asdict
from datetime import UTC, datetime

import asyncpg
import httpx

from app.core.config import get_settings
from app.core.db import postgres_dsn
from app.llm.client import open_llm_client
from app.llm.errors import LLMConfigurationError
from app.news.classify import classify_pending
from app.news.dedup import dedup_pending
from app.news.fetch import Fetcher, FetchError, FetchReport, load_sources, next_wakeup, run_once
from app.news.normalize import normalize_pending


def summary(report: FetchReport) -> dict[str, object]:
    return {
        "started_at": report.started_at.isoformat(),
        "new": report.new,
        "sources": [
            {"source": item.source_id, "new": item.new, "error": item.error}
            for item in report.sources
        ],
    }


async def crawl(only: str | None, loop: bool) -> None:
    settings = get_settings()
    sources = load_sources()
    connection = await asyncpg.connect(postgres_dsn(settings.database_url.get_secret_value()))
    try:
        async with httpx.AsyncClient() as http:
            fetcher = Fetcher(http=http, user_agent=settings.news_user_agent)
            while True:
                report = await run_once(connection, fetcher, sources, only=only)
                print(json.dumps(summary(report), ensure_ascii=False), flush=True)
                if not loop:
                    return
                wakeup = await next_wakeup(connection, sources, datetime.now(UTC))
                await asyncio.sleep(max(60.0, (wakeup - datetime.now(UTC)).total_seconds()))
    finally:
        await connection.close()


async def classify_news(limit: int, retry_rules: bool) -> None:
    settings = get_settings()
    connection = await asyncpg.connect(postgres_dsn(settings.database_url.get_secret_value()))
    try:
        try:
            async with open_llm_client(settings) as llm:
                report = await classify_pending(
                    connection, llm, limit=limit, retry_rules=retry_rules
                )
        except LLMConfigurationError as error:
            logging.warning("Модель не настроена, разбор по словарю: %s", error)
            report = await classify_pending(connection, None, limit=limit)
    finally:
        await connection.close()
    print(json.dumps(asdict(report), ensure_ascii=False, default=str))


async def deduplicate() -> None:
    settings = get_settings()
    connection = await asyncpg.connect(postgres_dsn(settings.database_url.get_secret_value()))
    try:
        report = await dedup_pending(connection)
    finally:
        await connection.close()
    print(json.dumps(asdict(report), ensure_ascii=False))


async def renormalize(everything: bool) -> None:
    settings = get_settings()
    sources = load_sources()
    connection = await asyncpg.connect(postgres_dsn(settings.database_url.get_secret_value()))
    try:
        report = await normalize_pending(connection, sources, everything=everything)
    finally:
        await connection.close()
    print(json.dumps(asdict(report), ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Новости: сбор (A3-01), нормализация (A3-02), сюжеты (A3-03), разбор (A3-04)"
    )
    parser.add_argument(
        "command", nargs="?", choices=["fetch", "normalize", "dedup", "classify"], default="fetch"
    )
    parser.add_argument("--source", help="fetch: обойти один источник вне расписания")
    parser.add_argument("--loop", action="store_true", help="fetch: обходить по расписанию")
    parser.add_argument("--all", action="store_true", help="normalize: пересчитать все записи")
    parser.add_argument("--limit", type=int, default=200, help="classify: сколько публикаций")
    parser.add_argument(
        "--retry-rules", action="store_true", help="classify: переразобрать моделью словарные"
    )
    args = parser.parse_args()
    logging.basicConfig(level=get_settings().log_level.upper())
    try:
        if args.command == "normalize":
            asyncio.run(renormalize(args.all))
        elif args.command == "dedup":
            asyncio.run(deduplicate())
        elif args.command == "classify":
            asyncio.run(classify_news(args.limit, args.retry_rules))
        else:
            asyncio.run(crawl(args.source, args.loop))
    except FetchError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
