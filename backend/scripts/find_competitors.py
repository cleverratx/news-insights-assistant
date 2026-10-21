"""Поиск реальных конкурентов для демо-легенды (задача A2-17).

Запрос к Wikidata по отраслям сегмента ОКВЭД: российские действующие компании, сначала
самые известные. Результат печатается блоком competitors для legends.yaml — его проверяют
руками: оставляют тех, кто действительно работает на рынке клиента.

    uv run python scripts/find_competitors.py 46.75 --region Москва
"""

from __future__ import annotations

import argparse
import asyncio

import httpx
import yaml

from app.profile.competitors import search_wikidata
from app.profile.okved import resolve


async def find(okved: str, region: str | None, limit: int) -> list[dict[str, str | None]]:
    segment = resolve(okved).segment.id
    async with httpx.AsyncClient() as client:
        found = await search_wikidata(client, segment, region, limit)
    return [
        {
            "name": item.name,
            "url": item.url,
            "region": item.region,
            "industry": item.industry,
        }
        for item in found
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Реальные конкуренты из Wikidata для легенды")
    parser.add_argument("okved", help="код ОКВЭД демо-компании, например 46.75")
    parser.add_argument("--region", help="регион демо-компании: местные игроки помечаются")
    parser.add_argument("-n", "--limit", type=int, default=15, help="сколько компаний вывести")
    args = parser.parse_args()
    segment = resolve(args.okved).segment
    competitors = asyncio.run(find(args.okved, args.region, args.limit))
    if not competitors:
        print(f"Для сегмента «{segment.name}» открытый поиск не настроен или ничего не нашёл")
        return
    print(f"# Сегмент «{segment.name}», найдено {len(competitors)}; проверьте руками")
    print(yaml.safe_dump({"competitors": competitors}, allow_unicode=True, sort_keys=False))


if __name__ == "__main__":
    main()
