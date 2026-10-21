from datetime import UTC, datetime, timedelta

import asyncpg
import pytest
from pydantic import ValidationError

from app.contracts.news import NewsStory, StoryItem, StoryMatch
from app.news.dedup import (
    Assignment,
    assign,
    cosine,
    dedup_pending,
    load_story,
    recent_stories,
    representative,
    stems,
    vectors,
)
from app.news.fetch import RawItem, Source, save_items
from app.news.fetch import ensure_schema as ensure_raw
from app.news.normalize import RawRecord, normalize, normalize_pending
from tests.test_data_loader import database

__all__ = ["database"]

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
CBR = Source(id="cbr", name="Банк России", kind="regulator", url="https://cbr.test/rss")
MEDIA = Source(id="media", name="Ведомости", kind="media", url="https://media.test/rss")
OTHER = Source(id="other", name="ПРАЙМ", kind="media", url="https://other.test/rss")
SOURCES = {source.id: source for source in (CBR, MEDIA, OTHER)}
NEWS = {
    "rate-cbr": ("cbr", "Банк России сохранил ключевую ставку на уровне 14%", 0),
    "rate-media": ("media", "ЦБ сохранил ключевую ставку 14% и ужесточил риторику", 1),
    "rate-copy": ("other", "ЦБ сохранил ключевую ставку 14% и ужесточил риторику", 2),
    "coal": ("other", "Экспорт угля из Кузбасса в Китай вырос на 12% за сентябрь", 3),
    "fuel": ("media", "Бензин на биржевых торгах подорожал на 3% за неделю", 4),
}
TEXTS = {
    "rate-cbr": "Совет директоров Банка России сохранил ключевую ставку 14% годовых.",
    "rate-media": "Совет директоров Банка России сохранил ключевую ставку 14% годовых.",
    "rate-copy": "Совет директоров Банка России сохранил ключевую ставку 14% годовых.",
    "coal": "Отгрузки угля из Кузбасса в Китай по железной дороге выросли на 12%.",
    "fuel": "Стоимость бензина Аи-95 на бирже выросла на 3% за неделю.",
}


def item(key: str, hours: float | None = None):  # type: ignore[no-untyped-def]
    source, title, offset = NEWS[key]
    published = NOW + timedelta(hours=offset if hours is None else hours)
    raw = RawRecord(
        raw_id=f"raw:{key}",
        source_id=source,
        url=f"https://{source}.test/{key}",
        title=title,
        fetched_at=published,
        feed_text=f"<p>{TEXTS[key]}</p>",
        published_at=published,
    )
    return normalize(raw, SOURCES[source]).item


def test_stems() -> None:
    assert stems("ЦБ сохранил ставку на уровне 14% и 0,05%") == [
        "сохра",
        "ставк",
        "уровн",
        "14%",
        "0,05%",
    ]


def test_similar_titles_are_close() -> None:
    items = [item(key) for key in NEWS]
    weights = vectors(items)
    assert cosine(weights["news:rate-cbr"], weights["news:rate-media"]) > 0.4
    assert cosine(weights["news:rate-cbr"], weights["news:coal"]) < 0.1


def test_assign_exact_similar_and_new() -> None:
    result = {found.news_id: found for found in assign([item(key) for key in NEWS])}
    story = result["news:rate-cbr"].cluster_id
    assert result["news:rate-cbr"].match is StoryMatch.FIRST
    assert story == "story:rate-cbr"
    assert result["news:rate-media"].cluster_id == story
    assert result["news:rate-media"].match is StoryMatch.SIMILAR
    copy = result["news:rate-copy"]
    assert (copy.cluster_id, copy.match, copy.duplicate_of) == (
        story,
        StoryMatch.EXACT,
        "news:rate-media",
    )
    assert result["news:coal"].cluster_id == "story:coal"
    assert result["news:fuel"].cluster_id == "story:fuel"


def test_window_limits_joining() -> None:
    late = item("rate-media", hours=80)
    result = {found.news_id: found for found in assign([item("rate-cbr"), late])}
    assert result["news:rate-media"].match is StoryMatch.FIRST


def test_known_assignments_are_kept() -> None:
    known = {"news:rate-cbr": Assignment("news:rate-cbr", "story:old", StoryMatch.FIRST)}
    result = assign([item("rate-cbr"), item("rate-media")], known)
    assert [found.news_id for found in result] == ["news:rate-media"]
    assert result[0].cluster_id == "story:old"


def test_representative_prefers_official_then_earliest() -> None:
    media, cbr = item("rate-media", hours=0), item("rate-cbr", hours=3)
    assert representative([media, cbr], {"Банк России"}).id == "news:rate-cbr"
    assert representative([media, cbr], set()).id == "news:rate-media"


def test_story_contract() -> None:
    entry = StoryItem(
        news_id="news:1",
        source="Ведомости",
        title="x",
        published_at=NOW,
        match=StoryMatch.FIRST,
    )
    with pytest.raises(ValidationError):
        NewsStory(
            id="story:1",
            representative_id="news:2",
            title="x",
            first_published_at=NOW,
            last_published_at=NOW,
            items=[entry],
        )


async def test_dedup_pending_end_to_end(database: asyncpg.Connection) -> None:
    await ensure_raw(database)
    raws = [
        RawItem(
            source_id=NEWS[key][0],
            url=f"https://{NEWS[key][0]}.test/{key}",
            title=NEWS[key][1],
            fetched_at=NOW + timedelta(hours=NEWS[key][2]),
            published_at=NOW + timedelta(hours=NEWS[key][2]),
            feed_text=f"<p>{TEXTS[key]}</p>",
        )
        for key in NEWS
    ]
    await save_items(database, raws)
    await normalize_pending(database, list(SOURCES.values()), now=NOW)
    report = await dedup_pending(database, now=NOW + timedelta(hours=5))
    assert (report.assigned, report.exact, report.similar, report.stories) == (5, 1, 1, 3)
    again = await dedup_pending(database, now=NOW + timedelta(hours=5))
    assert again.assigned == 0

    rate_id = raws[0].raw_id.replace("raw:", "story:")
    story = await load_story(database, rate_id, list(SOURCES.values()))
    assert story is not None
    assert story.representative_id == raws[0].raw_id.replace("raw:", "news:")
    assert story.title == "Банк России сохранил ключевую ставку на уровне 14%"
    assert [entry.source for entry in story.items] == ["Банк России", "Ведомости", "ПРАЙМ"]
    assert str(story.items[0].url) == "https://cbr.test/rate-cbr"

    stored = await database.fetchval(
        "SELECT item->'dedup'->>'cluster_id' FROM news_items WHERE news_id = $1",
        raws[1].raw_id.replace("raw:", "news:"),
    )
    assert stored == rate_id
    recent = await recent_stories(database, NOW, list(SOURCES.values()))
    assert len(recent) == 3
    assert await load_story(database, "story:none") is None
