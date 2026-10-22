import argparse
import json
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from app.agent.critic import Draft
from app.agent.tools import Decision, ToolResponse
from app.contracts import CompanyProfile, Insight, NewsItem, RankedItem
from app.contracts.competitors import Competitors
from app.contracts.critic import CriticReport
from app.contracts.feed import Feed
from app.contracts.knowledge import KnowledgeBase, KnowledgeGaps
from app.contracts.links import LinkedCompanies
from app.contracts.news import NewsCard, NewsStory
from app.contracts.related_feed import RelatedFeed
from app.contracts.search import SearchResult

SCHEMA_DIR = Path(__file__).resolve().parents[3] / "docs" / "schemas"

CONTRACTS: dict[str, type[BaseModel]] = {
    "insight_draft": Draft,
    "critic_report": CriticReport,
    "agent_decision": Decision,
    "tool_response": ToolResponse,
    "company_profile": CompanyProfile,
    "news_item": NewsItem,
    "ranked_item": RankedItem,
    "insight": Insight,
    "search_result": SearchResult,
    "knowledge_base": KnowledgeBase,
    "knowledge_gaps": KnowledgeGaps,
    "linked_companies": LinkedCompanies,
    "competitors": Competitors,
    "news_story": NewsStory,
    "news_card": NewsCard,
    "feed": Feed,
    "related_feed": RelatedFeed,
}


def render(name: str, model: type[BaseModel]) -> str:
    schema: dict[str, Any] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"{name}.schema.json",
        **model.model_json_schema(),
    }
    return json.dumps(schema, ensure_ascii=False, indent=2) + "\n"


def rendered() -> dict[Path, str]:
    return {
        SCHEMA_DIR / f"{name}.schema.json": render(name, model) for name, model in CONTRACTS.items()
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Выгрузка JSON-схем контрактов в docs/schemas")
    parser.add_argument("--check", action="store_true", help="только проверить актуальность")
    args = parser.parse_args()
    stale = []
    for path, content in rendered().items():
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if current == content:
            continue
        stale.append(path.name)
        if not args.check:
            path.write_text(content, encoding="utf-8", newline="\n")
    if args.check and stale:
        print(
            f"Схемы устарели: {', '.join(stale)}. Запустите python -m app.contracts",
            file=sys.stderr,
        )
        raise SystemExit(1)
    print("Схемы актуальны" if not stale else f"Обновлены: {', '.join(stale)}")


if __name__ == "__main__":
    main()
