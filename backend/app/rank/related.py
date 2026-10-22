"""Поток событий о компаниях из списков связей и конкурентов (A4-12).

Ищем конкретную компанию, а не похожую отрасль. Общая релевантность профилю
не отсекает событие о контрагенте. Новых вызовов LLM нет.
"""

import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from app.contracts.competitors import Competitors
from app.contracts.links import LinkedCompanies, LinkOrigin
from app.contracts.news import MaterialKind, NewsItem
from app.contracts.related_feed import CompanyMention, RelatedEvent, RelatedFeed, RelatedScope
from app.news.cards import card
from app.news.entities import ALIASES, canonical, entities_of


@dataclass(frozen=True)
class Target:
    id: str
    name: str
    key: str
    relationship: Literal["competitor", "counterparty"]


def mentions(news: NewsItem, targets: Sequence[Target]) -> list[CompanyMention]:
    entities = {
        e.key.removeprefix("company:"): e.name for e in entities_of(news) if e.kind == "company"
    }
    original = f"{news.title}\n{news.text}"
    characters: list[str] = []
    positions: list[int] = []
    for index, character in enumerate(original):
        if character in "«»\"“”„'":
            continue
        for lowered in character.lower().replace("ё", "е"):
            value = " " if lowered.isspace() else lowered
            if value == " " and characters and characters[-1] == " ":
                continue
            characters.append(value)
            positions.append(index)
    text = "".join(characters)
    found = []
    for target in targets:
        method: Literal["entity", "text"] = "entity"
        evidence = entities.get(target.key)
        if evidence is None:
            if len(target.key) < 3:
                continue
            names = {target.key, *(alias for alias, key in ALIASES.items() if key == target.key)}
            pattern = "|".join(
                re.escape(name) for name in sorted(names, key=lambda n: (-len(n), n))
            )
            match = re.search(rf"(?<!\w)(?:{pattern})(?!\w)", text)
            if match is None:
                continue
            method = "text"
            start, end = positions[match.start()], positions[match.end() - 1] + 1
            evidence = original[max(0, start - 70) : end + 70].strip()
        found.append(
            CompanyMention(
                id=target.id,
                name=target.name,
                relationship=target.relationship,
                method=method,
                evidence=evidence,
                news_id=news.id,
            )
        )
    return found


def build_related_feed(
    company_id: str,
    items: Sequence[NewsItem],
    competitors: Competitors,
    links: LinkedCompanies,
    *,
    now: datetime,
    days: int = 7,
    limit: int = 20,
    scope: RelatedScope = RelatedScope.ALL,
    shared: set[str] | None = None,
) -> RelatedFeed:
    if competitors.company_id != company_id or links.company_id != company_id:
        raise ValueError("Списки компаний относятся к другому клиенту")
    if not 1 <= days <= 14 or not 1 <= limit <= 50:
        raise ValueError("Период 1–14 дней, limit 1–50")
    targets: list[Target] = []
    unnamed = 0
    if scope is not RelatedScope.COUNTERPARTIES:
        for competitor in competitors.competitors:
            key = canonical(competitor.name or "")
            if not key:
                unnamed += 1
                continue
            targets.append(Target(competitor.id, competitor.name or "", key, "competitor"))
    cards = {c.id: c for c in links.cards}
    if scope is not RelatedScope.COMPETITORS:
        for linked in links.cards:
            if not (
                LinkOrigin.PAYMENTS in linked.origins
                or LinkOrigin.CLIENT in linked.origins
                or linked.role
            ):
                continue
            key = canonical(linked.name or "")
            if not key:
                unnamed += 1
                continue
            targets.append(Target(linked.id, linked.name or "", key, "counterparty"))
    since = now - timedelta(days=days)
    fresh = {
        item.id: item
        for item in items
        if item.kind is MaterialKind.NEWS and since <= item.published_at <= now
    }
    groups: dict[str, list[NewsItem]] = defaultdict(list)
    for news in fresh.values():
        groups[news.dedup.cluster_id or news.id].append(news)
    events = []
    for members in groups.values():
        found: dict[tuple[str, str], CompanyMention] = {}
        for news in sorted(members, key=lambda n: (-n.published_at.timestamp(), n.id)):
            for mention in mentions(news, targets):
                if mention.relationship == "counterparty":
                    linked = cards[mention.id]
                    mention = mention.model_copy(
                        update={"origins": linked.origins, "role": linked.role}
                    )
                mention_key = (mention.relationship, mention.id)
                previous = found.get(mention_key)
                if previous is None or (previous.method == "text" and mention.method == "entity"):
                    found[mention_key] = mention
        if not found:
            continue
        evidence_ids = {mention.news_id for mention in found.values()}
        representative = max(
            (n for n in members if n.id in evidence_ids), key=lambda n: (n.published_at, n.id)
        )
        events.append(
            RelatedEvent(
                news_id=representative.id,
                story_id=representative.dedup.cluster_id,
                card=card(representative, shared or set()),
                summary=representative.summary,
                companies=sorted(found.values(), key=lambda m: (m.relationship, m.id)),
                publications=[
                    card(n, shared or set())
                    for n in sorted(members, key=lambda n: (n.published_at, n.id), reverse=True)
                ],
            )
        )
    events.sort(key=lambda e: (fresh[e.news_id].published_at, e.news_id), reverse=True)
    warnings = list(dict.fromkeys([*competitors.warnings, *links.warnings]))
    if unnamed:
        warnings.append(f"Без публичного названия нельзя искать упоминания: {unnamed} компаний")
    if any(m.method == "text" for e in events for m in e.companies):
        warnings.append(
            "Текстовые совпадения названий не подтверждают юридическую идентичность компаний"
        )
    return RelatedFeed(
        company_id=company_id,
        generated_at=now,
        period_days=days,
        scope=scope,
        items=events[:limit],
        considered=len(fresh),
        matched=len(events),
        unnamed_companies=unnamed,
        warnings=warnings,
    )
