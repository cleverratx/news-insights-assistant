"""Серверные снимки ленты: повторное использование оценок без доверия клиентским баллам."""

import hashlib
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, ValidationError

from app.contracts.feed import Feed, FeedItem
from app.contracts.news import NewsItem
from app.contracts.profile import CompanyProfile

SCHEMA = """
CREATE TABLE IF NOT EXISTS feed_snapshots (
    snapshot_id text PRIMARY KEY,
    company_id text NOT NULL,
    created_at timestamptz NOT NULL,
    item jsonb NOT NULL
);
"""
INSERT = (
    "INSERT INTO feed_snapshots (snapshot_id, company_id, created_at, item) "
    "VALUES ($1, $2, $3, $4::jsonb)"
)
SELECT = "SELECT item FROM feed_snapshots WHERE snapshot_id = $1 AND company_id = $2"


class Snapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    feed: Feed
    profile_hash: str
    material_hashes: dict[str, str]


def digest(value: CompanyProfile | NewsItem) -> str:
    return hashlib.sha256(value.model_dump_json().encode()).hexdigest()


async def store(connection: Any, feed: Feed, profile: CompanyProfile, news: list[NewsItem]) -> Feed:
    snapshot_id = f"feed:{uuid4().hex}"
    feed = feed.model_copy(update={"snapshot_id": snapshot_id})
    visible = {card.news_id for card in feed.items}
    snapshot = Snapshot(
        feed=feed,
        profile_hash=digest(profile),
        material_hashes={n.id: digest(n) for n in news if n.id in visible},
    )
    await connection.execute(SCHEMA)
    await connection.execute(
        INSERT, snapshot_id, profile.company_id, feed.generated_at, snapshot.model_dump_json()
    )
    return feed


async def restore(
    connection: Any,
    snapshot_id: str,
    profile: CompanyProfile,
    news: list[NewsItem],
    role: str | None,
) -> list[FeedItem]:
    row = await connection.fetchrow(SELECT, snapshot_id, profile.company_id)
    if row is None:
        raise ValueError("Снимок ленты не найден для компании; обновите ленту")
    try:
        saved = Snapshot.model_validate_json(row["item"])
    except ValidationError:
        raise ValueError("Снимок ленты повреждён; обновите ленту") from None
    if saved.feed.company_id != profile.company_id or saved.feed.snapshot_id != snapshot_id:
        raise ValueError("Снимок ленты не соответствует компании; обновите ленту")
    if saved.profile_hash != digest(profile) or saved.feed.role != role:
        raise ValueError("Профиль или роль изменились; обновите ленту")
    cards = {item.news_id: item for item in saved.feed.items}
    if any(n.id not in cards or saved.material_hashes.get(n.id) != digest(n) for n in news):
        raise ValueError("Материал отсутствует в ленте или изменился; обновите ленту")
    return [cards[n.id] for n in news]
