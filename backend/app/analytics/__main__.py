import argparse
import asyncio
import json
from dataclasses import asdict
from pathlib import Path

import asyncpg

from app.analytics.ingest import ingest_dir
from app.core.config import get_settings
from app.core.db import postgres_dsn


async def ingest(folder: Path) -> None:
    settings = get_settings()
    connection = await asyncpg.connect(postgres_dsn(settings.database_url.get_secret_value()))
    try:
        report = await ingest_dir(connection, folder)
    finally:
        await connection.close()
    print(json.dumps(asdict(report), ensure_ascii=False, indent=2))


def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Загрузка аналитики Альфа-Банка из PDF (A3-06)")
    parser.add_argument(
        "--dir", type=Path, default=Path(settings.data_dir) / settings.analytics_dir
    )
    args = parser.parse_args()
    asyncio.run(ingest(args.dir))


if __name__ == "__main__":
    main()
