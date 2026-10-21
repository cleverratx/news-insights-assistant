from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg
import httpx
import pytest

from app.news.fetch import (
    FAILING_AFTER,
    Fetcher,
    FetchError,
    Robots,
    Source,
    SourceFile,
    SourceState,
    canonical_url,
    collect,
    due_sources,
    load_sources,
    next_state,
    parse_feed,
    run_once,
)
from tests.test_data_loader import database

__all__ = ["database"]

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
<channel><title>Тест</title>
<item><title>ЦБ сохранил ставку 14%</title><link>https://news.test/a1?utm_source=rss</link>
<guid>a1</guid><pubDate>Wed, 21 Oct 2026 10:00:00 +0300</pubDate>
<description>Совет директоров Банка России сохранил ключевую ставку</description></item>
<item><title>Дубль</title><link>https://news.test/a1</link></item>
<item><title>Экспорт угля вырос</title><link>https://news.test/a2</link>
<content:encoded><![CDATA[<p>Полный текст про уголь</p>]]></content:encoded></item>
<item><title>Без ссылки</title></item>
<item><title>Цены на сталь</title><link>https://news.test/a3</link></item>
</channel></rss>"""
ROBOTS = "User-agent: *\nDisallow: /a3\n"
SOURCE = Source(
    id="test", name="Тест", kind="media", url="https://news.test/rss", fetch_article=True
)


class Sleeps:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def fetcher(handler: httpx.MockTransport, sleeps: Sleeps | None = None) -> Fetcher:
    return Fetcher(
        http=httpx.AsyncClient(transport=handler),
        user_agent="alfa-news-insights/test",
        host_delay=0,
        sleep=sleeps or Sleeps(),
    )


def site(
    feed: str = FEED, robots: str | int = ROBOTS, article: int = 200
) -> tuple[httpx.MockTransport, list[str]]:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/robots.txt":
            if isinstance(robots, int):
                return httpx.Response(robots)
            return httpx.Response(200, text=robots)
        if request.url.path == "/rss":
            return httpx.Response(200, text=feed)
        return httpx.Response(article, text=f"<html><body>{request.url.path}</body></html>")

    return httpx.MockTransport(handler), seen


def test_project_sources_are_valid() -> None:
    sources = load_sources()
    ids = {source.id for source in sources}
    assert {"interfax", "cbr-press", "kommersant-economics"} <= ids
    assert all(set(source.backup_for) <= ids for source in sources)
    assert any(source.kind == "regulator" for source in sources)


def test_broken_sources_file(tmp_path: Path) -> None:
    broken = tmp_path / "sources.yaml"
    broken.write_text("sources: [", encoding="utf-8")
    with pytest.raises(FetchError):
        load_sources(broken)
    with pytest.raises(ValueError):
        SourceFile.model_validate({"sources": [SOURCE.model_dump() | {"backup_for": ["missing"]}]})


def state(source_id: str, failures: int = 0, due_in: int = -1) -> SourceState:
    return SourceState(
        source_id=source_id,
        last_attempt_at=NOW,
        next_due_at=NOW + timedelta(minutes=due_in),
        consecutive_failures=failures,
    )


def test_schedule_and_backup() -> None:
    main = SOURCE.model_copy(update={"id": "main"})
    other = SOURCE.model_copy(update={"id": "other"})
    backup = SOURCE.model_copy(update={"id": "backup", "backup_for": ["main", "other"]})
    off = SOURCE.model_copy(update={"id": "off", "enabled": False})
    sources = [main, other, backup, off]
    assert [item.id for item in due_sources(sources, {}, NOW)] == ["main", "other"]
    states = {"main": state("main", due_in=10)}
    assert [item.id for item in due_sources(sources, states, NOW)] == ["other"]
    failing = {
        "main": state("main", FAILING_AFTER, due_in=60),
        "other": state("other", FAILING_AFTER - 1),
    }
    assert "backup" not in [item.id for item in due_sources(sources, failing, NOW)]
    failing["other"] = state("other", FAILING_AFTER, due_in=60)
    assert [item.id for item in due_sources(sources, failing, NOW)] == ["backup"]


def test_next_state_backoff() -> None:
    ok = next_state(SOURCE, None, NOW)
    assert ok.next_due_at == NOW + timedelta(minutes=30)
    assert (ok.consecutive_failures, ok.last_success_at) == (0, NOW)
    first = next_state(SOURCE, ok, NOW, "HTTP 503")
    second = next_state(SOURCE, first, NOW, "HTTP 503")
    assert first.next_due_at - NOW == timedelta(minutes=5)
    assert second.next_due_at - NOW == timedelta(minutes=10)
    assert second.last_success_at == NOW
    many = SourceState("test", NOW, NOW, consecutive_failures=20)
    assert next_state(SOURCE, many, NOW, "x").next_due_at - NOW == timedelta(hours=6)


def test_canonical_url() -> None:
    assert canonical_url("https://News.test/a?utm_source=x&id=5#top") == "https://news.test/a?id=5"


def test_parse_feed() -> None:
    items = parse_feed(SOURCE, FEED.encode(), NOW)
    assert [item.url for item in items] == [
        "https://news.test/a1",
        "https://news.test/a2",
        "https://news.test/a3",
    ]
    first = items[0]
    assert first.published_at == datetime(2026, 10, 21, 7, 0, tzinfo=UTC)
    assert first.summary == "Совет директоров Банка России сохранил ключевую ставку"
    assert first.guid == "a1"
    assert items[1].feed_text == "<p>Полный текст про уголь</p>"
    limited = parse_feed(SOURCE.model_copy(update={"max_items": 1}), FEED.encode(), NOW)
    assert len(limited) == 1
    with pytest.raises(FetchError):
        parse_feed(SOURCE, b"<html>not a feed", NOW)


async def test_collect_fetches_articles_and_respects_robots() -> None:
    transport, seen = site()
    items = await collect(fetcher(transport), SOURCE, NOW)
    statuses = {item.url: item.article_status for item in items}
    assert statuses == {
        "https://news.test/a1": "fetched",
        "https://news.test/a2": "feed_only",
        "https://news.test/a3": "robots",
    }
    assert items[0].html is not None and "/a1" in items[0].html
    assert "/a3" not in seen and "/a2" not in seen


async def test_collect_skips_known_urls() -> None:
    transport, seen = site()

    async def known(urls: list[str]) -> set[str]:
        return {"https://news.test/a1"}

    items = await collect(fetcher(transport), SOURCE, NOW, known)
    assert "https://news.test/a1" not in [item.url for item in items]
    assert "/a1" not in seen


async def test_robots_missing_allows_and_down_forbids() -> None:
    transport, _ = site(robots=404)
    assert len(await collect(fetcher(transport), SOURCE, NOW)) == 3
    transport, _ = site(robots=503)
    with pytest.raises(FetchError, match="robots"):
        await collect(fetcher(transport), SOURCE, NOW)


async def test_retries_temporary_errors_with_retry_after() -> None:
    answers = [httpx.Response(503, headers={"Retry-After": "7"}), httpx.Response(200, text="ok")]

    def handler(request: httpx.Request) -> httpx.Response:
        return answers.pop(0)

    sleeps = Sleeps()
    response = await fetcher(httpx.MockTransport(handler), sleeps).get("https://news.test/x")
    assert response.text == "ok"
    assert sleeps.calls == [7.0]


async def test_no_retry_on_client_error() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(404)

    with pytest.raises(FetchError) as error:
        await fetcher(httpx.MockTransport(handler)).get("https://news.test/x")
    assert error.value.status == 404
    assert len(calls) == 1


async def test_transport_errors_exhaust_retries() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timeout")

    sleeps = Sleeps()
    with pytest.raises(FetchError, match="ConnectTimeout"):
        await fetcher(httpx.MockTransport(handler), sleeps).get("https://news.test/x")
    assert sleeps.calls == [1.0, 2.0]


async def test_pause_between_requests_to_one_host() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    sleeps = Sleeps()
    ticks = iter([0.0, 0.2, 1.0, 5.0, 5.0])
    client = Fetcher(
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        user_agent="test",
        host_delay=1.0,
        sleep=sleeps,
        clock=lambda: next(ticks),
    )
    await client.get("https://news.test/1")
    await client.get("https://news.test/2")
    await client.get("https://news.test/3")
    assert sleeps.calls == [pytest.approx(0.8)]


async def test_run_once_saves_and_schedules(database: asyncpg.Connection) -> None:
    transport, _ = site()
    client = fetcher(transport)
    report = await run_once(database, client, [SOURCE], NOW)
    assert report.new == 3
    rows = await database.fetch("SELECT url, article_status FROM raw_news ORDER BY url")
    assert [row["article_status"] for row in rows] == ["fetched", "feed_only", "robots"]

    again = await run_once(database, client, [SOURCE], NOW + timedelta(minutes=5))
    assert again.sources == []
    later = await run_once(database, client, [SOURCE], NOW + timedelta(minutes=31))
    assert later.new == 0 and later.sources[0].error is None

    down, _ = site(robots=503)
    failed = await run_once(database, fetcher(down), [SOURCE], NOW + timedelta(hours=2))
    assert failed.sources[0].error is not None
    saved = await database.fetchrow("SELECT * FROM news_sources_state WHERE source_id = 'test'")
    assert saved["consecutive_failures"] == 1
    assert saved["last_success_at"] == NOW + timedelta(minutes=31)


async def test_run_one_source_out_of_schedule(database: asyncpg.Connection) -> None:
    transport, _ = site()
    client = fetcher(transport)
    await run_once(database, client, [SOURCE], NOW)
    forced = await run_once(database, client, [SOURCE], NOW, only="test")
    assert [item.source_id for item in forced.sources] == ["test"]


KOMMERSANT_LIKE = """User-agent: *
Disallow: /?
Disallow: /*utm_
Disallow: /doc*=
Allow: /doc/print$
Disallow: /doc/print
Clean-param: x /doc/

User-agent: SemrushBot
Disallow: /
User-agent: alfa-news-insights
User-agent: OtherBot
Disallow: /private
"""


def test_robots_google_rules() -> None:
    robots = Robots.parse(KOMMERSANT_LIKE.splitlines())
    agent = "SomeBot/1.0"
    assert robots.allowed(agent, "https://k.test/rss/section-economics.xml")
    assert not robots.allowed(agent, "https://k.test/?page=2")
    assert not robots.allowed(agent, "https://k.test/doc/1?utm_source=x")
    assert not robots.allowed(agent, "https://k.test/doc?id=5")
    assert robots.allowed(agent, "https://k.test/doc/print")
    assert not robots.allowed(agent, "https://k.test/doc/print/2")
    assert not robots.allowed("SemrushBot/7", "https://k.test/news")
    ours = "alfa-news-insights/0.1"
    assert robots.allowed(ours, "https://k.test/doc?id=5")
    assert not robots.allowed(ours, "https://k.test/private/x")
    assert Robots.parse([]).allowed(agent, "https://k.test/any")
