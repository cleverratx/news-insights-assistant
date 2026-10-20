import argparse
import asyncio
import json
import sys
from pathlib import Path

import asyncpg

from app.core.config import get_settings
from app.core.db import postgres_dsn
from app.data.loader import CaseData, read_case, store
from app.data.reader import CaseFileError

COMPANIES_FILE = "df_Uid_data_hack.csv"
LINKS_FILE = "df_Fid_Uid_link_hack.csv"


async def load(data: CaseData) -> dict[str, object]:
    dsn = postgres_dsn(get_settings().database_url.get_secret_value())
    connection = await asyncpg.connect(dsn)
    try:
        return await store(connection, data)
    finally:
        await connection.close()


def main() -> None:
    data_dir = Path(get_settings().data_dir)
    raw = data_dir / "raw"
    parser = argparse.ArgumentParser(description="Загрузка выгрузок кейса в PostgreSQL (A2-01)")
    parser.add_argument("--companies", type=Path, default=raw / COMPANIES_FILE)
    parser.add_argument("--links", type=Path, default=raw / LINKS_FILE)
    parser.add_argument("--payments", type=Path, help="отдельный файл платежей, если он появится")
    parser.add_argument("--dry-run", action="store_true", help="только разобрать и показать отчёт")
    parser.add_argument(
        "--samples", action="store_true", help="взять синтетические выгрузки из data/samples"
    )
    args = parser.parse_args()
    if args.samples:
        args.companies = data_dir / "samples" / COMPANIES_FILE
        args.links = data_dir / "samples" / LINKS_FILE
    try:
        data = read_case(args.companies, args.links if args.links.exists() else None, args.payments)
    except CaseFileError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
    report = data.report() if args.dry_run else asyncio.run(load(data))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
