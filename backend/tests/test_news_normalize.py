import json
from datetime import UTC, datetime, timedelta

import asyncpg
from selectolax.parser import HTMLParser

from app.contracts.news import MaterialKind
from app.news.fetch import RawItem, Source, save_items
from app.news.fetch import ensure_schema as ensure_raw
from app.news.normalize import (
    RawRecord,
    content_hash,
    generic_paragraphs,
    language,
    load_item,
    normalize,
    normalize_pending,
    split_long,
)
from tests.test_data_loader import database

__all__ = ["database"]

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
SOURCE = Source(
    id="test",
    name="Тестовые новости",
    kind="media",
    url="https://news.test/rss",
    text_selector=".body p",
)
PARAGRAPHS = [
    "Совет директоров Банка России 24 октября сохранил ключевую ставку на уровне 14% годовых"
    " и сообщил, что денежно-кредитные условия останутся жёсткими до конца года.",
    "Регулятор отметил, что инфляционное давление остаётся высоким, а риторика стала жёстче:"
    " снижение ставки на ближайших заседаниях совет директоров не рассматривал.",
]
LD = json.dumps({"@type": "NewsArticle", "articleBody": "Текст статьи из JSON-LD. " * 20})
PAGE = f"""<html><head><title>x</title>
<script type="application/ld+json">{LD}</script>
</head><body>
<header><nav>Главная Новости Экономика Финансы Политика Общество Спорт Культура</nav></header>
<div class="body"><p>{PARAGRAPHS[0]}</p><p>  </p><p>Читайте также: другая новость про ставку ЦБ</p>
<p>{PARAGRAPHS[1]}</p><p>{PARAGRAPHS[1]}</p></div>
<footer>© 2026 Тестовые новости. Все права защищены. Полное или частичное копирование</footer>
</body></html>"""


def raw(**changes: object) -> RawRecord:
    data: dict[str, object] = {
        "raw_id": "raw:abc",
        "source_id": "test",
        "url": "https://news.test/a1",
        "title": "  ЦБ&nbsp;сохранил ставку  ",
        "fetched_at": NOW,
        "summary": "<p>Совет директоров сохранил ставку</p>",
        "html": PAGE,
        "published_at": NOW - timedelta(hours=2),
    }
    return RawRecord(**(data | changes))  # type: ignore[arg-type]


def test_selector_text_and_fields() -> None:
    result = normalize(raw(), SOURCE)
    item = result.item
    assert result.text_origin == "selector"
    assert item.id == "news:abc"
    assert item.kind is MaterialKind.NEWS
    assert item.title == "ЦБ сохранил ставку"
    assert item.text == "\n\n".join(PARAGRAPHS)
    assert [fragment.id for fragment in item.fragments] == ["p1", "p2"]
    assert item.fragments[1].locator == "абзац 2"
    assert item.source.name == "Тестовые новости"
    assert str(item.url) == "https://news.test/a1"
    assert item.language == "ru"
    assert item.published_at == NOW - timedelta(hours=2)
    assert item.collected_at == NOW
    assert item.summary == "Совет директоров сохранил ставку"
    assert item.classification is None


def test_fallbacks_in_order() -> None:
    no_selector = SOURCE.model_copy(update={"text_selector": None})
    assert normalize(raw(), no_selector).text_origin == "json_ld"
    plain = PAGE.replace('<script type="application/ld+json">', "<script>")
    generic = normalize(raw(html=plain), no_selector)
    assert generic.text_origin == "generic"
    assert generic.item.text == "\n\n".join(PARAGRAPHS)
    feed = normalize(raw(feed_text="<p>Полный текст из ленты, которого достаточно</p>"), SOURCE)
    assert (feed.text_origin, feed.item.text) == (
        "feed",
        "Полный текст из ленты, которого достаточно",
    )
    summary = normalize(raw(html=None), SOURCE)
    assert (summary.text_origin, summary.item.text) == (
        "summary",
        "Совет директоров сохранил ставку",
    )
    assert summary.item.summary is None
    title = normalize(raw(html=None, summary=None), None)
    assert (title.text_origin, title.item.text) == ("title", "ЦБ сохранил ставку")
    assert title.item.source.name == "test"


def test_future_or_missing_date_falls_back_to_collection_time() -> None:
    assert normalize(raw(published_at=None), SOURCE).item.published_at == NOW
    future = normalize(raw(published_at=NOW + timedelta(days=1)), SOURCE)
    assert future.item.published_at == NOW


def test_generic_finds_article_body() -> None:
    rows = [
        "Первый абзац статьи про рост цен на сталь и прокат в России.",
        "Второй абзац статьи: металлурги повысили цены на арматуру на 5%.",
        "Третий абзац: аналитики ждут продолжения роста до конца года.",
    ]
    body = "".join(f'<div class="a"><div class="p">{row}</div></div>' for row in rows)
    menu = '<div class="menu"><div>Кнопка меню с длинным текстом про всё подряд</div></div>'
    page = f"<html><body>{menu}<main>{body}</main></body></html>"
    paragraphs = generic_paragraphs(HTMLParser(page))
    assert len(paragraphs) == 3
    assert paragraphs[0].startswith("Первый абзац")


def test_split_long_text() -> None:
    sentence = "Это предложение про экономику и рынок нефти в России. "
    chunks = split_long(sentence * 60)
    assert len(chunks) > 3
    assert all(len(chunk) < 700 for chunk in chunks)
    assert split_long("Короткий текст.") == ["Короткий текст."]


def test_language_and_hash() -> None:
    assert language("Банк России сохранил ставку") == "ru"
    assert language("Fed keeps rates unchanged") == "en"
    assert language("2026 — 14%") == "unknown"
    assert content_hash("ЦБ сохранил ставку", "Текст.") == content_hash(
        "цб  сохранил ставку!", "текст"
    )
    assert content_hash("ЦБ сохранил ставку", "Текст") != content_hash("ЦБ поднял ставку", "Текст")


async def test_normalize_pending_stores_items(database: asyncpg.Connection) -> None:
    await ensure_raw(database)
    items = [
        RawItem(
            source_id="test",
            url=f"https://news.test/a{index}",
            title=f"Новость {index}",
            fetched_at=NOW,
            html=PAGE,
            article_status="fetched",
        )
        for index in range(3)
    ]
    await save_items(database, items)
    report = await normalize_pending(database, [SOURCE], now=NOW)
    assert report.normalized == 3
    assert report.by_origin == {"selector": 3}
    again = await normalize_pending(database, [SOURCE], now=NOW)
    assert again.normalized == 0
    item = await load_item(database, items[0].raw_id.replace("raw:", "news:"))
    assert item is not None and item.text == "\n\n".join(PARAGRAPHS)

    changed = SOURCE.model_copy(update={"text_selector": ".missing"})
    redo = await normalize_pending(database, [changed], everything=True, now=NOW)
    assert redo.by_origin == {"json_ld": 3}
    count = await database.fetchval("SELECT count(*) FROM news_items")
    assert count == 3
