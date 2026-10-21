"""Карточка оригинала (A3-12): издание, дата, ссылка на статью и картинка.

Картинка берётся по порядку: из ленты (enclosure, media:content — у Коммерсанта и Ведомостей
это фото статьи), og:image страницы, twitter:image, link rel=image_src, первая картинка
в полном тексте ленты. Ссылка доводится до абсолютной и проверяется: только http(s), без
data-URI, SVG и пикселей счётчиков.

Отсутствие картинки не должно ломать вёрстку, поэтому image в карточке либо проверенная
ссылка, либо null, и тогда у карточки всегда есть чем заполнить место: инициалы и домен
издания (placeholder). Картинка, которая стоит у издания на многих статьях, — это логотип
или заглушка сайта («/common/images/share-1.jpg» у ЦБ), а не фото: в карточку она не идёт.
"""

import re
from collections.abc import Iterable, Sequence
from typing import Any
from urllib.parse import urljoin, urlsplit

from pydantic import HttpUrl, ValidationError
from selectolax.parser import HTMLParser

from app.contracts.news import NewsCard, NewsItem, Placeholder

SHARED_IMAGE = 3
META = (
    'meta[property="og:image:secure_url"]',
    'meta[property="og:image"]',
    'meta[name="og:image"]',
    'meta[name="twitter:image"]',
    'meta[property="twitter:image"]',
)
TRACKING = re.compile(r"(pixel|counter|spacer|blank|1x1|tracking|logo)", re.IGNORECASE)
EXTENSION = re.compile(r"\.(svg|gif|ico)(\?|$)", re.IGNORECASE)
COLORS = ("#EF3124", "#1B6EF3", "#0B9E5A", "#8A3FFC", "#F2994A", "#2D9CDB", "#6B7280")


def usable(url: str | None, base: str) -> str | None:
    """Абсолютная http(s)-ссылка на картинку или None: data-URI, SVG, иконки и пиксели
    счётчиков в карточку не годятся."""
    if not url or url.strip().startswith("data:"):
        return None
    absolute = urljoin(base, url.strip())
    parts = urlsplit(absolute)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    if EXTENSION.search(parts.path) or TRACKING.search(parts.path):
        return None
    try:
        HttpUrl(absolute)
    except ValidationError:
        return None
    return absolute


def page_image(html: str) -> str | None:
    tree = HTMLParser(html)
    for selector in META:
        node = tree.css_first(selector)
        content = node.attributes.get("content") if node else None
        if content:
            return content
    link = tree.css_first('link[rel="image_src"]')
    return link.attributes.get("href") if link else None


def text_image(markup: str) -> str | None:
    node = HTMLParser(markup).css_first("img[src]")
    return node.attributes.get("src") if node else None


def pick_image(
    url: str, feed_image: str | None, html: str | None, feed_text: str | None
) -> str | None:
    """Картинка статьи: первая годная по порядку источников."""
    candidates = [
        feed_image,
        page_image(html) if html else None,
        text_image(feed_text) if feed_text else None,
    ]
    for candidate in candidates:
        found = usable(candidate, url)
        if found:
            return found
    return None


def as_url(value: str | None) -> HttpUrl | None:
    return HttpUrl(value) if value else None


def domain(url: str | None) -> str | None:
    host = urlsplit(url or "").netloc.lower()
    return host.removeprefix("www.") or None


def initials(name: str) -> str:
    """Буквы для заглушки: «Российская газета» → «РГ», «Интерфакс» → «И»."""
    words = [word for word in re.split(r"[\s—–-]+", name) if word and word[0].isalnum()]
    letters = "".join(word[0] for word in words[:2]).upper()
    return letters or "?"


def placeholder(item: NewsItem) -> Placeholder:
    name = item.source.name
    color = COLORS[sum(map(ord, name)) % len(COLORS)]
    return Placeholder(
        initials=initials(name),
        domain=domain(str(item.url) if item.url else None) or name,
        color=color,
    )


def card(item: NewsItem, shared: Iterable[str] = ()) -> NewsCard:
    image = str(item.image) if item.image else None
    if image in set(shared):
        image = None
    return NewsCard(
        news_id=item.id,
        title=item.title,
        source=item.source.name,
        published_at=item.published_at,
        url=item.url,
        image=as_url(image),
        placeholder=placeholder(item),
    )


SHARED_SQL = """
SELECT item->>'image' AS image FROM news_items
WHERE item->>'image' IS NOT NULL
GROUP BY source_id, item->>'image'
HAVING count(*) >= $1
"""
CARDS_SQL = "SELECT item FROM news_items WHERE news_id = ANY($1::text[])"


async def shared_images(connection: Any, repeats: int = SHARED_IMAGE) -> set[str]:
    """Картинки, которые у одного издания стоят на нескольких статьях: логотипы и заглушки."""
    return {record["image"] for record in await connection.fetch(SHARED_SQL, repeats)}


async def cards(connection: Any, news_ids: Sequence[str]) -> list[NewsCard]:
    """Карточки в порядке news_ids; неизвестные id пропускаются."""
    shared = await shared_images(connection)
    found = {
        item.id: item
        for item in (
            NewsItem.model_validate_json(record["item"])
            for record in await connection.fetch(CARDS_SQL, list(news_ids))
        )
    }
    return [card(found[news_id], shared) for news_id in news_ids if news_id in found]
