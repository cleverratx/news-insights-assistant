"""Сбор новостей (A3-01): обход RSS из sources.yaml по расписанию.

Вежливость к источникам: robots.txt, пауза между запросами к одному сайту, повторы с
нарастающей паузой только на временных ошибках (таймаут, 429, 5xx) и Retry-After от сервера.
Если источник падает несколько обходов подряд, его место занимают резервные (backup_for),
а сам он проверяется реже.

Сохраняем как есть: запись ленты, ссылку, а для лент с одним заголовком — HTML страницы
статьи. Разметку снимает нормализация (A3-02): исходник остаётся, чтобы её можно было
пересчитать, не обходя источники заново.
"""

import asyncio
import calendar
import hashlib
import logging
import re
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

import feedparser
import httpx
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.core.config import get_settings
from app.news.issues import decode_html

BACKEND_ROOT = Path(__file__).resolve().parents[2]
FAILING_AFTER = 3
FAILURE_BACKOFF = timedelta(minutes=5)
MAX_FAILURE_PAUSE = timedelta(hours=6)
HOST_DELAY_SECONDS = 1.0
RETRIES = 2
RETRY_BASE_SECONDS = 1.0
MAX_RETRY_AFTER_SECONDS = 60.0
TIMEOUT_SECONDS = 20.0
MAX_BODY_BYTES = 3_000_000
RETRYABLE = {429, 500, 502, 503, 504}

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_news (
    raw_id text PRIMARY KEY,
    source_id text NOT NULL,
    url text NOT NULL UNIQUE,
    guid text,
    title text NOT NULL,
    summary text,
    feed_text text,
    html text,
    published_at timestamptz,
    fetched_at timestamptz NOT NULL,
    article_status text NOT NULL
);
CREATE INDEX IF NOT EXISTS raw_news_source ON raw_news (source_id, fetched_at);
CREATE TABLE IF NOT EXISTS news_sources_state (
    source_id text PRIMARY KEY,
    last_attempt_at timestamptz NOT NULL,
    last_success_at timestamptz,
    consecutive_failures integer NOT NULL DEFAULT 0,
    last_error text,
    next_due_at timestamptz NOT NULL
);
"""
INSERT_SQL = """
INSERT INTO raw_news (raw_id, source_id, url, guid, title, summary, feed_text, html,
                      published_at, fetched_at, article_status)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
ON CONFLICT (url) DO NOTHING
"""
KNOWN_SQL = "SELECT url FROM raw_news WHERE url = ANY($1::text[])"
STATES_SQL = "SELECT * FROM news_sources_state"
STATE_SQL = """
INSERT INTO news_sources_state (source_id, last_attempt_at, last_success_at,
                                consecutive_failures, last_error, next_due_at)
VALUES ($1, $2, $3, $4, $5, $6)
ON CONFLICT (source_id) DO UPDATE SET
    last_attempt_at = EXCLUDED.last_attempt_at,
    last_success_at = coalesce(EXCLUDED.last_success_at, news_sources_state.last_success_at),
    consecutive_failures = EXCLUDED.consecutive_failures,
    last_error = EXCLUDED.last_error,
    next_due_at = EXCLUDED.next_due_at
"""

ArticleStatus = Literal["feed_only", "fetched", "robots", "failed", "too_large"]


class FetchError(Exception):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str = Field(min_length=1)
    kind: Literal["media", "regulator"]
    url: str = Field(pattern=r"^https?://")
    interval_minutes: int = Field(default=30, ge=5, le=24 * 60)
    fetch_article: bool = False
    max_items: int = Field(default=30, ge=1, le=200)
    backup_for: list[str] = Field(default_factory=list)
    text_selector: str | None = None
    enabled: bool = True


class SourceFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sources: list[Source] = Field(min_length=1)

    @model_validator(mode="after")
    def consistent(self) -> "SourceFile":
        ids = [source.id for source in self.sources]
        if len(ids) != len(set(ids)):
            raise ValueError("Повторяются id источников")
        for source in self.sources:
            unknown = [ref for ref in source.backup_for if ref not in ids or ref == source.id]
            if unknown:
                raise ValueError(f"{source.id}: неизвестные backup_for: {', '.join(unknown)}")
        return self


def sources_path(setting: str | None = None) -> Path:
    path = Path(setting or get_settings().news_sources_file)
    return path if path.is_absolute() else BACKEND_ROOT / path


def load_sources(path: Path | None = None) -> list[Source]:
    """Источники из YAML. Битый файл — ошибка запуска, а не тихо пустая лента. Интервал
    по умолчанию — NEWS_FETCH_INTERVAL_MINUTES."""
    target = path or sources_path()
    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8"))
        interval = get_settings().news_fetch_interval_minutes
        for entry in raw.get("sources", []) if isinstance(raw, dict) else []:
            if isinstance(entry, dict):
                entry.setdefault("interval_minutes", interval)
        return SourceFile.model_validate(raw).sources
    except (OSError, yaml.YAMLError, ValidationError) as error:
        raise FetchError(f"Список источников {target} не прочитан: {error}") from error


@dataclass(frozen=True)
class SourceState:
    source_id: str
    last_attempt_at: datetime
    next_due_at: datetime
    consecutive_failures: int = 0
    last_success_at: datetime | None = None
    last_error: str | None = None

    @property
    def failing(self) -> bool:
        return self.consecutive_failures >= FAILING_AFTER


def due_sources(
    sources: Sequence[Source], states: dict[str, SourceState], now: datetime
) -> list[Source]:
    """Кого обходить сейчас. Резервный источник — только пока все его основные недоступны."""

    def failing(source_id: str) -> bool:
        state = states.get(source_id)
        return state is not None and state.failing

    def is_due(source: Source) -> bool:
        state = states.get(source.id)
        return state is None or state.next_due_at <= now

    result = []
    for source in sources:
        if not source.enabled:
            continue
        if source.backup_for and not all(failing(ref) for ref in source.backup_for):
            continue
        if is_due(source):
            result.append(source)
    return result


def next_state(
    source: Source,
    previous: SourceState | None,
    now: datetime,
    error: str | None = None,
) -> SourceState:
    """Успех — следующий обход через интервал источника. Ошибка — через паузу, которая растёт
    с каждой неудачей подряд, но не дольше шести часов."""
    if error is None:
        return SourceState(
            source_id=source.id,
            last_attempt_at=now,
            next_due_at=now + timedelta(minutes=source.interval_minutes),
            last_success_at=now,
        )
    failures = (previous.consecutive_failures if previous else 0) + 1
    pause = min(FAILURE_BACKOFF * 2 ** (failures - 1), MAX_FAILURE_PAUSE)
    return SourceState(
        source_id=source.id,
        last_attempt_at=now,
        next_due_at=now + pause,
        consecutive_failures=failures,
        last_success_at=previous.last_success_at if previous else None,
        last_error=error[:500],
    )


@dataclass(frozen=True)
class RawItem:
    """Запись ленты, как её отдал источник, и при необходимости HTML страницы статьи."""

    source_id: str
    url: str
    title: str
    fetched_at: datetime
    guid: str | None = None
    summary: str | None = None
    feed_text: str | None = None
    html: str | None = None
    published_at: datetime | None = None
    article_status: ArticleStatus = "feed_only"

    @property
    def raw_id(self) -> str:
        return raw_id(self.url)


def raw_id(url: str) -> str:
    return "raw:" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]


def canonical_url(url: str) -> str:
    """Ссылка без меток и якоря: одна статья не сохраняется дважды из-за utm_source."""
    parts = urlsplit(url.strip())
    query = "&".join(
        pair
        for pair in parts.query.split("&")
        if pair and not pair.lower().startswith(("utm_", "from=", "yclid=", "fbclid="))
    )
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path, query, ""))


def entry_time(entry: Any) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        value = entry.get(key)
        if value:
            return datetime.fromtimestamp(calendar.timegm(value), UTC)
    return None


def entry_text(entry: Any) -> str | None:
    """Полный текст, если лента его отдаёт: content:encoded или yandex:full-text."""
    contents = [item.get("value", "") for item in entry.get("content", [])]
    candidates = [*contents, entry.get("yandex_full-text", ""), entry.get("full-text", "")]
    best = max(candidates, key=len, default="")
    return best or None


def parse_feed(source: Source, body: bytes, now: datetime) -> list[RawItem]:
    feed = feedparser.parse(body)
    if feed.bozo and not feed.entries:
        raise FetchError(f"Лента не разобрана: {feed.get('bozo_exception')}")
    items: dict[str, RawItem] = {}
    for entry in feed.entries:
        link = entry.get("link")
        title = (entry.get("title") or "").strip()
        if not link or not title:
            continue
        url = canonical_url(link)
        items.setdefault(
            url,
            RawItem(
                source_id=source.id,
                url=url,
                title=title,
                fetched_at=now,
                guid=entry.get("id"),
                summary=entry.get("summary") or None,
                feed_text=entry_text(entry),
                published_at=entry_time(entry),
            ),
        )
        if len(items) >= source.max_items:
            break
    return list(items.values())


Sleep = Callable[[float], Awaitable[None]]


@dataclass
class Fetcher:
    """HTTP для обхода: паузы между запросами к одному сайту, повторы и robots.txt."""

    http: httpx.AsyncClient
    user_agent: str
    host_delay: float = HOST_DELAY_SECONDS
    retries: int = RETRIES
    retry_base: float = RETRY_BASE_SECONDS
    sleep: Sleep = asyncio.sleep
    clock: Callable[[], float] = time.monotonic
    last_request: dict[str, float] = field(default_factory=dict)
    robots: dict[str, "Robots | None"] = field(default_factory=dict)

    async def pace(self, host: str) -> None:
        previous = self.last_request.get(host)
        if previous is not None:
            wait = self.host_delay - (self.clock() - previous)
            if wait > 0:
                await self.sleep(wait)
        self.last_request[host] = self.clock()

    async def get(self, url: str) -> httpx.Response:
        host = urlsplit(url).netloc
        for attempt in range(self.retries + 1):
            await self.pace(host)
            try:
                response = await self.http.get(
                    url,
                    headers={"User-Agent": self.user_agent},
                    timeout=TIMEOUT_SECONDS,
                    follow_redirects=True,
                )
            except httpx.TransportError as error:
                if attempt == self.retries:
                    raise FetchError(f"{url}: {type(error).__name__}") from error
                await self.sleep(self.retry_base * 2**attempt)
                continue
            if response.status_code in RETRYABLE and attempt < self.retries:
                await self.sleep(max(self.retry_base * 2**attempt, retry_after(response)))
                continue
            if response.status_code >= 400:
                raise FetchError(f"{url}: HTTP {response.status_code}", response.status_code)
            return response
        raise AssertionError("Недостижимое состояние повторов")

    async def allowed(self, url: str) -> bool:
        """robots.txt сайта. Нет файла (4xx) — можно; сайт не ответил — не ходим."""
        parts = urlsplit(url)
        host = parts.netloc
        if host not in self.robots:
            robots_url = urlunsplit((parts.scheme, host, "/robots.txt", "", ""))
            lines: list[str] | None
            try:
                lines = (await self.get(robots_url)).text.splitlines()
            except FetchError as error:
                lines = [] if error.status is not None and error.status < 500 else None
            self.robots[host] = Robots.parse(lines) if lines is not None else None
        rules = self.robots[host]
        return rules is not None and rules.allowed(self.user_agent, url)


@dataclass(frozen=True)
class Robots:
    """robots.txt по правилам Google: группа самого точного User-agent, шаблоны * и $,
    побеждает самое длинное правило, при равенстве — Allow. urllib.robotparser не годится:
    он не понимает * и превращает «Disallow: /?» в запрет всего сайта."""

    groups: dict[str, list[tuple[bool, str]]]

    @classmethod
    def parse(cls, lines: Sequence[str]) -> "Robots":
        groups: dict[str, list[tuple[bool, str]]] = {}
        agents: list[str] = []
        in_rules = False
        for raw in lines:
            line = raw.split("#", 1)[0].strip()
            name, _, value = line.partition(":")
            name, value = name.strip().lower(), value.strip()
            if name == "user-agent":
                if in_rules:
                    agents, in_rules = [], False
                agents.append(value.lower())
                for agent in agents:
                    groups.setdefault(agent, [])
            elif name in {"allow", "disallow"} and agents:
                in_rules = True
                if value:
                    for agent in agents:
                        groups[agent].append((name == "allow", value))
        return cls(groups)

    def rules_for(self, user_agent: str) -> list[tuple[bool, str]]:
        token = user_agent.split("/", 1)[0].lower()
        named = [agent for agent in self.groups if agent != "*" and agent in token]
        if named:
            return self.groups[max(named, key=len)]
        return self.groups.get("*", [])

    def allowed(self, user_agent: str, url: str) -> bool:
        parts = urlsplit(url)
        target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        best: tuple[int, bool] = (-1, True)
        for allow, pattern in self.rules_for(user_agent):
            if robots_match(pattern, target) and (len(pattern), allow) > best:
                best = (len(pattern), allow)
        return best[1]


def robots_match(pattern: str, target: str) -> bool:
    anchored = pattern.endswith("$")
    body = re.escape(pattern.rstrip("$")).replace(r"\*", ".*")
    return re.match(body + ("$" if anchored else ""), target) is not None


def retry_after(response: httpx.Response) -> float:
    try:
        return min(float(response.headers.get("Retry-After", 0)), MAX_RETRY_AFTER_SECONDS)
    except ValueError:
        return 0.0


async def with_article(fetcher: Fetcher, item: RawItem) -> RawItem:
    """Страница статьи для ленты без текста. Ошибка страницы не роняет обход источника."""
    if not await fetcher.allowed(item.url):
        return replace_item(item, article_status="robots")
    try:
        response = await fetcher.get(item.url)
    except FetchError as error:
        logger.info("Статья не скачана: %s", error)
        return replace_item(item, article_status="failed")
    if len(response.content) > MAX_BODY_BYTES:
        return replace_item(item, article_status="too_large")
    page = decode_html(response.content, response.headers.get("content-type"))
    return replace_item(item, html=page, article_status="fetched")


def replace_item(item: RawItem, **changes: Any) -> RawItem:
    return RawItem(**{**item.__dict__, **changes})


Known = Callable[[list[str]], Awaitable[set[str]]]


async def collect(
    fetcher: Fetcher, source: Source, now: datetime, known: Known | None = None
) -> list[RawItem]:
    """Новые записи источника. Уже сохранённые ссылки пропускаем, их страницы не качаем."""
    if not await fetcher.allowed(source.url):
        raise FetchError(f"{source.url}: обход запрещён robots.txt")
    response = await fetcher.get(source.url)
    items = parse_feed(source, response.content, now)
    if known is not None:
        seen = await known([item.url for item in items])
        items = [item for item in items if item.url not in seen]
    if source.fetch_article:
        items = [
            await with_article(fetcher, item) if not item.feed_text else item for item in items
        ]
    return items


@dataclass
class SourceReport:
    source_id: str
    new: int = 0
    error: str | None = None


@dataclass
class FetchReport:
    started_at: datetime
    sources: list[SourceReport] = field(default_factory=list)

    @property
    def new(self) -> int:
        return sum(report.new for report in self.sources)


async def ensure_schema(connection: Any) -> None:
    await connection.execute(SCHEMA)


async def load_states(connection: Any) -> dict[str, SourceState]:
    return {
        record["source_id"]: SourceState(
            source_id=record["source_id"],
            last_attempt_at=record["last_attempt_at"],
            next_due_at=record["next_due_at"],
            consecutive_failures=record["consecutive_failures"],
            last_success_at=record["last_success_at"],
            last_error=record["last_error"],
        )
        for record in await connection.fetch(STATES_SQL)
    }


async def save_state(connection: Any, state: SourceState) -> None:
    await connection.execute(
        STATE_SQL,
        state.source_id,
        state.last_attempt_at,
        state.last_success_at,
        state.consecutive_failures,
        state.last_error,
        state.next_due_at,
    )


async def save_items(connection: Any, items: Sequence[RawItem]) -> int:
    saved = 0
    for item in items:
        status = await connection.execute(
            INSERT_SQL,
            item.raw_id,
            item.source_id,
            item.url,
            item.guid,
            item.title,
            item.summary,
            item.feed_text,
            item.html,
            item.published_at,
            item.fetched_at,
            item.article_status,
        )
        saved += status == "INSERT 0 1"
    return saved


async def known_urls(connection: Any, urls: Sequence[str]) -> set[str]:
    return {record["url"] for record in await connection.fetch(KNOWN_SQL, list(urls))}


async def run_once(
    connection: Any,
    fetcher: Fetcher,
    sources: Sequence[Source],
    now: datetime | None = None,
    only: str | None = None,
) -> FetchReport:
    """Один обход: все источники, которым пора, или один выбранный (only) вне расписания."""
    moment = now or datetime.now(UTC)
    await ensure_schema(connection)
    states = await load_states(connection)
    chosen = (
        [source for source in sources if source.id == only]
        if only
        else due_sources(sources, states, moment)
    )
    report = FetchReport(started_at=moment)
    for source in chosen:
        result = SourceReport(source.id)
        try:
            fresh = await collect(
                fetcher, source, moment, lambda urls: known_urls(connection, urls)
            )
            result.new = await save_items(connection, fresh)
            state = next_state(source, states.get(source.id), moment)
        except FetchError as error:
            result.error = str(error)
            state = next_state(source, states.get(source.id), moment, str(error))
            logger.warning("Источник %s недоступен: %s", source.id, error)
        except Exception as error:
            result.error = f"{type(error).__name__}: {error}"
            state = next_state(source, states.get(source.id), moment, result.error)
            logger.exception("Источник %s: сбой обработки, обход продолжается", source.id)
        await save_state(connection, state)
        report.sources.append(result)
    return report


async def next_wakeup(connection: Any, sources: Sequence[Source], now: datetime) -> datetime:
    """Когда следующему источнику пора: для режима обхода по расписанию."""
    states = await load_states(connection)
    moments = [
        states[source.id].next_due_at if source.id in states else now
        for source in sources
        if source.enabled
    ]
    return min(moments, default=now + timedelta(minutes=30))
