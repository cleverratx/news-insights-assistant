import argparse
import asyncio
import logging

import asyncpg

from app.contracts.news import Marker, MaterialKind
from app.core.config import get_settings
from app.core.db import postgres_dsn
from app.llm.client import open_llm_client
from app.llm.errors import LLMConfigurationError
from app.search.service import search


async def run(
    query: str, kinds: list[MaterialKind], limit: int, rerank: bool, markers: list[Marker]
) -> None:
    settings = get_settings()
    connection = await asyncpg.connect(postgres_dsn(settings.database_url.get_secret_value()))
    try:
        if not rerank:
            result = await search(
                connection, query, None, kinds=kinds, limit=limit, markers=markers
            )
        else:
            try:
                async with open_llm_client(settings) as llm:
                    result = await search(
                        connection, query, llm, kinds=kinds, limit=limit, markers=markers
                    )
            except LLMConfigurationError as error:
                logging.warning("Модель не настроена, только BM25: %s", error)
                result = await search(
                    connection, query, None, kinds=kinds, limit=limit, markers=markers
                )
    finally:
        await connection.close()
    print(result.model_dump_json(indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Поиск по аналитике банка и новостям (A3-07)")
    parser.add_argument("query")
    parser.add_argument("--bank", action="store_true", help="только аналитика Альфа-Банка")
    parser.add_argument("--news", action="store_true", help="только новости")
    parser.add_argument("-n", "--limit", type=int, default=5)
    parser.add_argument(
        "--marker",
        action="append",
        default=[],
        choices=[marker.value for marker in Marker],
        help="только материалы с маркером; можно несколько",
    )
    parser.add_argument("--no-rerank", action="store_true", help="без модели, только BM25")
    args = parser.parse_args()
    kinds = [MaterialKind.BANK_ANALYTICS, MaterialKind.NEWS]
    if args.bank != args.news:
        kinds = [MaterialKind.BANK_ANALYTICS] if args.bank else [MaterialKind.NEWS]
    markers = [Marker(value) for value in args.marker]
    asyncio.run(run(args.query, kinds, args.limit, not args.no_rerank, markers))


if __name__ == "__main__":
    main()
