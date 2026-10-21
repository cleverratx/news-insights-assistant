from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from app.news import pipeline
from app.news.classify import classify_pending
from app.news.fetch import RawItem, Source, save_items
from app.news.fetch import ensure_schema as ensure_raw
from app.news.issues import decode_html, paywalled, repair
from app.news.normalize import RawRecord, normalize, normalize_pending
from app.news.pipeline import run_pipeline
from tests.test_data_loader import database

__all__ = ["database"]

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
SOURCE = Source(
    id="media", name="Ведомости", kind="media", url="https://media.test/rss", text_selector="p"
)
TEXT = "Банк России сохранил ключевую ставку на уровне 14% годовых и ужесточил сигнал рынку."
PAYWALL = (
    "<html><body><p>Банк России сохранил ставку, и это важно для заёмщиков бизнеса.</p>"
    "<div>Вы видите 13% этого материала. Подпишитесь, чтобы дочитать статью.</div>"
    "</body></html>"
)


def raw(**changes: object) -> RawRecord:
    data: dict[str, object] = {
        "raw_id": "raw:a",
        "source_id": "media",
        "url": "https://media.test/a",
        "title": "ЦБ сохранил ставку",
        "fetched_at": NOW,
        "summary": "Анонс: ЦБ сохранил ставку 14%",
        "html": f"<html><body><p>{TEXT}</p></body></html>",
        "published_at": NOW - timedelta(hours=1),
    }
    return RawRecord(**(data | changes))  # type: ignore[arg-type]


def kinds(result: object) -> list[str]:
    return [issue.kind for issue in result.issues]  # type: ignore[attr-defined]


def test_decode_html() -> None:
    page = f"<html><p>{TEXT}</p></html>"
    assert decode_html(page.encode("cp1251")) == page
    assert decode_html(page.encode("utf-8")) == page
    assert decode_html(page.encode("cp1251"), "text/html; charset=windows-1251") == page
    meta = f'<html><head><meta charset="windows-1251"></head><p>{TEXT}</p></html>'
    assert decode_html(meta.encode("cp1251")) == meta
    assert decode_html(page.encode("utf-8"), "text/html; charset=no-such") == page


def test_repair_mojibake() -> None:
    broken = TEXT.encode("utf-8").decode("cp1251")
    fixed, issue = repair(broken)
    assert fixed == TEXT and issue is not None and issue.kind == "encoding_fixed"
    latin, issue = repair("Сбер".encode().decode("latin-1"))
    assert latin == "Сбер" and issue is not None
    assert repair(TEXT) == (TEXT, None)
    assert repair("ascii only") == ("ascii only", None)
    damaged, issue = repair("Банк �� России")
    assert issue is not None and issue.kind == "encoding"
    assert repair(None) == (None, None)


def test_paywall_detection() -> None:
    assert paywalled(PAYWALL)
    advert = "<p>Хотите скрыть рекламу? Оформите подписку и читайте, не отвлекаясь</p>"
    assert not paywalled(advert)
    assert not paywalled('<script>var paywall = true;</script><p class="paywall">Текст</p>')
    assert not paywalled(None)


def test_normalize_reports_material_issues() -> None:
    assert kinds(normalize(raw(), SOURCE)) == []
    assert kinds(normalize(raw(published_at=None), SOURCE)) == ["no_date"]
    future = normalize(raw(published_at=NOW + timedelta(days=2)), SOURCE)
    assert kinds(future) == ["no_date"] and future.item.published_at == NOW
    empty = normalize(raw(html="<html><body><nav>Меню</nav></body></html>"), SOURCE)
    assert kinds(empty) == ["empty_page"] and empty.text_origin == "summary"
    closed = normalize(raw(html=PAYWALL), SOURCE)
    assert kinds(closed) == ["paywall"]
    assert closed.item.text.startswith("Банк России сохранил ставку, и это важно")
    mojibake = normalize(raw(title="ЦБ сохранил ставку".encode().decode("cp1251")), SOURCE)
    assert kinds(mojibake) == ["encoding_fixed"]
    assert mojibake.item.title == "ЦБ сохранил ставку"


async def seed(database: asyncpg.Connection, urls: list[str]) -> None:
    await ensure_raw(database)
    await save_items(
        database,
        [
            RawItem(
                source_id="media",
                url=url,
                title=f"Новость {index}: ЦБ и ставка",
                fetched_at=NOW,
                published_at=None if index == 0 else NOW,
                html=f"<html><body><p>{TEXT} Номер {index}.</p></body></html>",
            )
            for index, url in enumerate(urls)
        ],
    )


async def test_one_broken_record_does_not_stop_normalization(
    database: asyncpg.Connection,
) -> None:
    await seed(database, ["https://media.test/1", "https://media.test/2", "not a url"])
    report = await normalize_pending(database, [SOURCE], now=NOW)
    assert (report.normalized, report.failed) == (2, 1)
    assert report.issues == {"no_date": 1}
    errors = await database.fetch("SELECT kind, detail FROM news_issues WHERE kind = 'error'")
    assert len(errors) == 1 and "ValidationError" in errors[0]["detail"]
    for _ in range(2):
        await normalize_pending(database, [SOURCE], now=NOW)
    gave_up = await normalize_pending(database, [SOURCE], now=NOW)
    assert (gave_up.normalized, gave_up.failed) == (0, 0)


async def test_classify_skips_broken_item(database: asyncpg.Connection) -> None:
    await seed(database, ["https://media.test/1"])
    await normalize_pending(database, [SOURCE], now=NOW)
    await database.execute(
        "INSERT INTO news_items (news_id, raw_id, source_id, url, title, published_at, "
        "collected_at, language, content_hash, text_origin, item, normalized_at) "
        "VALUES ('news:bad', 'raw:bad', 'media', 'x', 'x', $1, $1, 'ru', 'h', 'title', "
        "'{\"broken\": true}'::jsonb, $1)",
        NOW + timedelta(hours=1),
    )
    report = await classify_pending(database, None, now=NOW)
    assert (report.classified, report.failed) == (1, 1)


async def test_pipeline_survives_failed_stage(
    database: asyncpg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    await seed(database, ["https://media.test/1", "https://media.test/2"])

    async def broken(*args: object, **kwargs: object) -> None:
        raise RuntimeError("сюжеты сломались")

    monkeypatch.setattr(pipeline, "dedup_pending", broken)
    report = await run_pipeline(database, None, None, [SOURCE], now=NOW)
    assert report.failed == ["dedup"]
    assert report.stages["dedup"] == {"error": "RuntimeError: сюжеты сломались"}
    assert report.stages["normalize"]["normalized"] == 2
    assert report.stages["classify"]["classified"] == 2
    assert report.stages["extract"]["stories"] == 2
    assert report.issues == {"dedup": {"error": 1}, "normalize": {"no_date": 1}}
