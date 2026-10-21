from datetime import UTC, datetime, timedelta

import asyncpg
import feedparser

from app.contracts.news import NewsCard
from app.news.cards import (
    card,
    cards,
    initials,
    page_image,
    pick_image,
    placeholder,
    shared_images,
    usable,
)
from app.news.fetch import RawItem, Source, entry_image, save_items
from app.news.fetch import ensure_schema as ensure_raw
from app.news.normalize import RawRecord, normalize, normalize_pending
from tests.test_data_loader import database

__all__ = ["database"]

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
SOURCE = Source(id="media", name="Российская газета", kind="media", url="https://rg.test/rss")
URL = "https://www.rg.test/2026/10/22/coal.html"
PAGE = """<html><head>
<meta property="og:image" content="/uploads/2026/coal.jpg">
<meta name="twitter:image" content="https://rg.test/twitter.jpg">
</head><body><p>Отгрузки угля в Китай выросли на 12% за сентябрь.</p></body></html>"""
FEED = """<?xml version="1.0"?>
<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/"><channel>
<item><title>A</title><link>https://k.test/a</link>
<enclosure url="https://img.k.test/a.jpg" type="image/jpeg" length="1"/></item>
<item><title>B</title><link>https://k.test/b</link>
<media:content url="https://img.k.test/b.jpg" medium="image"/></item>
<item><title>C</title><link>https://k.test/c</link>
<enclosure url="https://k.test/c.mp3" type="audio/mpeg" length="1"/></item>
</channel></rss>"""


def test_feed_images() -> None:
    entries = feedparser.parse(FEED).entries
    assert [entry_image(entry) for entry in entries] == [
        "https://img.k.test/a.jpg",
        "https://img.k.test/b.jpg",
        None,
    ]


def test_usable_images() -> None:
    assert usable("/uploads/a.jpg", URL) == "https://www.rg.test/uploads/a.jpg"
    assert usable("//cdn.rg.test/a.jpg", URL) == "https://cdn.rg.test/a.jpg"
    assert usable("data:image/png;base64,AAAA", URL) is None
    assert usable("https://rg.test/icons/logo.svg", URL) is None
    assert usable("https://counter.test/pixel.gif?id=1", URL) is None
    assert usable("javascript:alert(1)", URL) is None
    assert usable(None, URL) is None


def test_pick_image_order() -> None:
    assert page_image(PAGE) == "/uploads/2026/coal.jpg"
    assert pick_image(URL, "https://img.rg.test/photo.jpg", PAGE, None) == (
        "https://img.rg.test/photo.jpg"
    )
    assert pick_image(URL, None, PAGE, None) == "https://www.rg.test/uploads/2026/coal.jpg"
    feed_text = '<p>Текст</p><img src="https://img.rg.test/inline.jpg">'
    assert pick_image(URL, None, None, feed_text) == "https://img.rg.test/inline.jpg"
    assert pick_image(URL, "data:x", "<html></html>", None) is None


def item(**changes: object):  # type: ignore[no-untyped-def]
    data: dict[str, object] = {
        "raw_id": "raw:coal",
        "source_id": "media",
        "url": URL,
        "title": "Экспорт угля вырос",
        "fetched_at": NOW,
        "html": PAGE,
        "published_at": NOW,
    }
    return normalize(RawRecord(**(data | changes)), SOURCE).item  # type: ignore[arg-type]


def test_card_with_and_without_image() -> None:
    with_image = card(item())
    assert str(with_image.image) == "https://www.rg.test/uploads/2026/coal.jpg"
    assert (with_image.source, with_image.published_at, str(with_image.url)) == (
        "Российская газета",
        NOW,
        URL,
    )
    bare = card(
        item(html="<html><body><p>Текст статьи без картинки, но вполне длинный.</p></body></html>")
    )
    assert bare.image is None
    assert bare.placeholder.initials == "РГ"
    assert bare.placeholder.domain == "rg.test"
    assert bare.placeholder == placeholder(item())
    assert NewsCard.model_validate(bare.model_dump()) == bare
    logo = card(item(), shared={"https://www.rg.test/uploads/2026/coal.jpg"})
    assert logo.image is None


def test_initials() -> None:
    assert initials("Российская газета") == "РГ"
    assert initials("Интерфакс") == "И"
    assert initials("Банк России — пресс-релизы") == "БР"
    assert initials("") == "?"


async def test_shared_images_are_treated_as_logo(database: asyncpg.Connection) -> None:
    await ensure_raw(database)
    raws = [
        RawItem(
            source_id="media",
            url=f"https://www.rg.test/{index}.html",
            title=f"Новость {index}",
            fetched_at=NOW + timedelta(minutes=index),
            published_at=NOW,
            html='<html><head><meta property="og:image" content="/common/share.jpg"></head>'
            f"<body><p>Длинный текст новости номер {index} про экономику.</p></body></html>",
        )
        for index in range(3)
    ]
    raws.append(
        RawItem(
            source_id="media",
            url="https://www.rg.test/photo.html",
            title="Новость с фото",
            fetched_at=NOW,
            published_at=NOW,
            image_url="https://img.rg.test/photo.jpg",
            html="<html><body><p>Текст новости с настоящей фотографией статьи.</p></body></html>",
        )
    )
    await save_items(database, raws)
    await normalize_pending(database, [SOURCE], now=NOW)
    assert await shared_images(database) == {"https://www.rg.test/common/share.jpg"}
    ids = [raw.raw_id.replace("raw:", "news:") for raw in raws]
    found = await cards(database, [ids[3], ids[0], "news:missing"])
    assert [str(found[0].image), found[1].image] == ["https://img.rg.test/photo.jpg", None]
    assert len(found) == 2
