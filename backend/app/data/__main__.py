import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import asyncpg

from app.core.config import get_settings
from app.core.db import postgres_dsn
from app.data.loader import CaseData, read_case, store
from app.data.reader import CaseFileError

COMPANIES_FILE = "df_Uid_data_hack.csv"
LINKS_FILE = "df_Fid_Uid_link_hack.csv"
PAYMENTS_FILE = "df_Uid_payment_hack.csv"


@dataclass(frozen=True)
class CaseInputs:
    companies: Path
    links: Path | None
    payments: Path | None


def resolve_inputs(
    data_dir: Path,
    companies: Path | None = None,
    links: Path | None = None,
    payments: Path | None = None,
    samples: bool = False,
) -> CaseInputs:
    folder = data_dir / ("samples" if samples else "raw")
    if samples:
        companies = folder / COMPANIES_FILE
        links = folder / LINKS_FILE
    companies = companies or folder / COMPANIES_FILE
    links = links or folder / LINKS_FILE

    if payments is not None and not payments.exists():
        raise CaseFileError(f"Файл платежей не найден: {payments}")
    if payments is None:
        candidate = links.parent / PAYMENTS_FILE
        payments = candidate if candidate.exists() else None

    return CaseInputs(
        companies=companies,
        links=links if links.exists() else None,
        payments=payments,
    )


async def load(data: CaseData) -> dict[str, object]:
    dsn = postgres_dsn(get_settings().database_url.get_secret_value())
    connection = await asyncpg.connect(dsn)
    try:
        return await store(connection, data)
    finally:
        await connection.close()


def main() -> None:
    data_dir = Path(get_settings().data_dir)
    parser = argparse.ArgumentParser(description="Загрузка выгрузок кейса в PostgreSQL (A2-01)")
    parser.add_argument(
        "--companies", type=Path, help=f"по умолчанию {data_dir / 'raw' / COMPANIES_FILE}"
    )
    parser.add_argument("--links", type=Path, help=f"по умолчанию {data_dir / 'raw' / LINKS_FILE}")
    parser.add_argument(
        "--payments",
        type=Path,
        help=f"отдельный файл платежей; по умолчанию {PAYMENTS_FILE} рядом с файлом связей",
    )
    parser.add_argument("--dry-run", action="store_true", help="только разобрать и показать отчёт")
    parser.add_argument(
        "--samples", action="store_true", help="взять синтетические выгрузки из data/samples"
    )
    args = parser.parse_args()
    try:
        inputs = resolve_inputs(data_dir, args.companies, args.links, args.payments, args.samples)
        data = read_case(inputs.companies, inputs.links, inputs.payments)
    except CaseFileError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
    report = data.report() if args.dry_run else asyncio.run(load(data))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
