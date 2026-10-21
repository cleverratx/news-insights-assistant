import argparse
import asyncio
import json
import logging
import sys
from datetime import UTC, datetime

import asyncpg
import httpx

from app.core.config import get_settings
from app.core.db import postgres_dsn
from app.news.fetch import Fetcher, FetchError, FetchReport, load_sources, next_wakeup, run_once


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


def main() -> None:
    parser = argparse.ArgumentParser(description="Сбор новостей из источников (A3-01)")
    parser.add_argument("--source", help="обойти один источник вне расписания")
    parser.add_argument("--loop", action="store_true", help="обходить по расписанию, не выходя")
    args = parser.parse_args()
    logging.basicConfig(level=get_settings().log_level.upper())
    try:
        asyncio.run(crawl(args.source, args.loop))
    except FetchError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
