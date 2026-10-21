"""Нормализация (A3-02): запись raw_news превращается в NewsItem — дата, заголовок, текст
без разметки абзацами, источник, ссылка, язык.

Текст статьи берём в таком порядке:

1. полный текст из ленты (content:encoded, yandex:full-text);
2. абзацы по CSS-селектору источника (text_selector в sources.yaml);
3. articleBody из JSON-LD страницы;
4. общий алгоритм: блоки текста без вложенных блоков, побеждает узел, под которым больше
   всего текста — так находится тело статьи без меню и подвала;
5. анонс из ленты.

Абзацы сохраняются фрагментами: на них потом ссылаются факты и выводы.
"""

import hashlib
import html
import json
import re
from collections import defaultdict
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast

from pydantic import HttpUrl
from selectolax.parser import HTMLParser, Node

from app.contracts.news import Channel, Dedup, Fragment, MaterialKind, NewsItem
from app.contracts.news import Source as SourceRef
from app.news.cards import as_url, pick_image
from app.news.fetch import Source
from app.news.issues import Issue, failure, paywalled, repair
from app.news.issues import ensure_schema as ensure_issues
from app.news.issues import record as record_issue

STAGE = "normalize"
MIN_PARAGRAPH = 40
MIN_ARTICLE = 300
LONG_PARAGRAPH = 1500
CHUNK = 600
SENTENCE_END = re.compile(r"(?<=[.!?…])\s+(?=[А-ЯЁA-Z«\"])")
MAX_FRAGMENTS = 80
MAX_TEXT = 30_000
FUTURE_SLACK = timedelta(hours=1)
DROP = "script, style, nav, header, footer, aside, form, noscript, figure, button, svg, iframe"
BLOCKS = {"p", "div", "ul", "ol", "li", "table", "section", "article", "h1", "h2", "h3"}
SPACE = re.compile(r"\s+")
BOILERPLATE = re.compile(
    r"^(читайте также|читать также|подписывайтесь|подпишитесь|реклама\b|фото( на превью)?:"
    r"|источник фото|©|все права защищены)",
    re.IGNORECASE,
)
CYRILLIC = re.compile(r"[а-яё]", re.IGNORECASE)
LATIN = re.compile(r"[a-z]", re.IGNORECASE)

TextOrigin = Literal["feed", "selector", "json_ld", "generic", "summary", "title"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS news_items (
    news_id text PRIMARY KEY,
    raw_id text NOT NULL UNIQUE,
    source_id text NOT NULL,
    url text NOT NULL,
    title text NOT NULL,
    published_at timestamptz NOT NULL,
    collected_at timestamptz NOT NULL,
    language text NOT NULL,
    content_hash text NOT NULL,
    text_origin text NOT NULL,
    item jsonb NOT NULL,
    normalized_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS news_items_published ON news_items (published_at DESC);
CREATE INDEX IF NOT EXISTS news_items_hash ON news_items (content_hash);
"""
PENDING_SQL = """
SELECT r.* FROM raw_news r
LEFT JOIN news_items n ON n.raw_id = r.raw_id
WHERE n.raw_id IS NULL
  AND (SELECT count(*) FROM news_issues i
       WHERE i.raw_id = r.raw_id AND i.stage = 'normalize' AND i.kind = 'error') < 3
ORDER BY r.fetched_at
LIMIT $1
"""
ALL_SQL = "SELECT * FROM raw_news ORDER BY fetched_at LIMIT $1"
UPSERT_SQL = """
INSERT INTO news_items (news_id, raw_id, source_id, url, title, published_at, collected_at,
                        language, content_hash, text_origin, item, normalized_at)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb, $12)
ON CONFLICT (raw_id) DO UPDATE SET
    title = EXCLUDED.title,
    published_at = EXCLUDED.published_at,
    language = EXCLUDED.language,
    content_hash = EXCLUDED.content_hash,
    text_origin = EXCLUDED.text_origin,
    item = EXCLUDED.item,
    normalized_at = EXCLUDED.normalized_at
"""


def clean(value: str) -> str:
    return SPACE.sub(" ", html.unescape(value).replace("\xa0", " ")).strip()


def strip_markup(value: str | None) -> str:
    if not value:
        return ""
    tree = HTMLParser(value)
    root = tree.body or tree.root
    return clean(root.text(separator=" ")) if root is not None else clean(value)


def node_key(node: Node) -> int:
    return cast(int, node.mem_id)


def descendants(node: Node) -> Iterator[Node]:
    """Вложенные элементы. Node.traverse() не годится: он идёт и дальше по документу."""
    for child in node.iter():
        yield child
        yield from descendants(child)


def is_leaf(node: Node) -> bool:
    return not any(child.tag in BLOCKS for child in descendants(node))


def split_long(text: str) -> list[str]:
    """Сплошной текст (articleBody без переносов) — кусками по несколько предложений:
    на фрагмент должна быть возможна точная ссылка."""
    if len(text) <= LONG_PARAGRAPH:
        return [text]
    chunks: list[str] = []
    for sentence in SENTENCE_END.split(text):
        if chunks and len(chunks[-1]) + len(sentence) < CHUNK:
            chunks[-1] = f"{chunks[-1]} {sentence}"
        else:
            chunks.append(sentence)
    return chunks


def keep(paragraphs: Sequence[str], minimum: int = 1) -> list[str]:
    """Без пустых, служебных строк и повторов: вложенные селекторы дают один абзац дважды."""
    result: list[str] = []
    seen: set[str] = set()
    for paragraph in paragraphs:
        text = clean(paragraph)
        if len(text) < minimum or BOILERPLATE.match(text) or text in seen:
            continue
        if result and (text in result[-1] or result[-1] in text):
            result[-1] = max(result[-1], text, key=len)
            seen.add(text)
            continue
        seen.add(text)
        result.append(text)
    return result


def html_paragraphs(markup: str) -> list[str]:
    """Абзацы куска HTML: из полного текста ленты, где разметка простая."""
    tree = HTMLParser(markup)
    blocks = [node.text(separator=" ") for node in tree.css("p, li, h2, h3") if is_leaf(node)]
    if blocks:
        return keep(blocks)
    root = tree.body or tree.root
    text = root.text(separator="\n") if root is not None else markup
    return keep(text.split("\n"))


def selector_paragraphs(tree: HTMLParser, selector: str) -> list[str]:
    return keep([node.text(separator=" ") for node in tree.css(selector)])


def json_ld_body(tree: HTMLParser) -> list[str]:
    for node in tree.css('script[type="application/ld+json"]'):
        try:
            data: Any = json.loads(node.text())
        except ValueError:
            continue
        items = data if isinstance(data, list) else data.get("@graph", [data])
        for item in items:
            body = item.get("articleBody") if isinstance(item, dict) else None
            if isinstance(body, str) and body.strip():
                parts = re.split(r"\n+|(?<=[.!?])\s{2,}", body)
                return keep([chunk for part in parts for chunk in split_long(clean(part))])
    return []


def generic_paragraphs(tree: HTMLParser) -> list[str]:
    """Тело статьи без подсказок: у каждого блока текста без вложенных блоков вес идёт родителю
    и вполовину прародителю, берём блоки под узлом с наибольшим весом."""
    for node in tree.css(DROP):
        node.decompose()
    root = tree.body or tree.root
    if root is None:
        return []
    blocks: list[tuple[Node, str]] = []
    for node in descendants(root):
        if node.tag in BLOCKS and is_leaf(node):
            text = clean(node.text(separator=" "))
            if len(text) >= MIN_PARAGRAPH:
                blocks.append((node, text))
    scores: defaultdict[int, float] = defaultdict(float)
    nodes: dict[int, Node] = {}
    for node, text in blocks:
        parent = node.parent
        for weight in (1.0, 0.5):
            if parent is None:
                break
            scores[node_key(parent)] += len(text) * weight
            nodes[node_key(parent)] = parent
            parent = parent.parent
    if not scores:
        return []
    best = nodes[max(scores, key=lambda key: scores[key])]
    inside = {node_key(node) for node in descendants(best)}
    return keep([text for node, text in blocks if node_key(node) in inside], MIN_PARAGRAPH)


def article_text(raw: "RawRecord", selector: str | None) -> tuple[list[str], TextOrigin]:
    if raw.feed_text:
        paragraphs = html_paragraphs(raw.feed_text)
        if paragraphs:
            return paragraphs, "feed"
    if raw.html:
        page = raw.html
        found: list[tuple[list[str], TextOrigin]] = []
        steps: list[tuple[TextOrigin, Callable[[], list[str]]]] = [
            ("selector", lambda: selector_paragraphs(HTMLParser(page), selector or "")),
            ("json_ld", lambda: json_ld_body(HTMLParser(page))),
            ("generic", lambda: generic_paragraphs(HTMLParser(page))),
        ]
        for origin, extract_text in steps:
            if origin == "selector" and not selector:
                continue
            paragraphs = extract_text()
            if sum(map(len, paragraphs)) >= MIN_ARTICLE:
                return paragraphs, origin
            if paragraphs:
                found.append((paragraphs, origin))
        if found:
            return max(found, key=lambda pair: sum(map(len, pair[0])))
    summary = strip_markup(raw.summary)
    if summary:
        return [summary], "summary"
    return [clean(raw.title)], "title"


def language(text: str) -> str:
    cyrillic, latin = len(CYRILLIC.findall(text)), len(LATIN.findall(text))
    if cyrillic + latin == 0:
        return "unknown"
    return "ru" if cyrillic >= latin else "en"


def content_hash(title: str, text: str) -> str:
    """Хеш смысла, а не байтов: регистр, пунктуация и пробелы не влияют. Для поиска точных
    перепечаток в дедупликации (A3-03)."""
    words = re.findall(r"\w+", f"{title} {text}".lower())
    return hashlib.sha256(" ".join(words).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RawRecord:
    raw_id: str
    source_id: str
    url: str
    title: str
    fetched_at: datetime
    summary: str | None = None
    feed_text: str | None = None
    html: str | None = None
    published_at: datetime | None = None
    image_url: str | None = None

    @classmethod
    def from_record(cls, record: Any) -> "RawRecord":
        return cls(
            raw_id=record["raw_id"],
            source_id=record["source_id"],
            url=record["url"],
            title=record["title"],
            fetched_at=record["fetched_at"],
            summary=record["summary"],
            feed_text=record["feed_text"],
            html=record["html"],
            published_at=record["published_at"],
            image_url=record.get("image_url"),
        )


@dataclass(frozen=True)
class Normalized:
    item: NewsItem
    raw_id: str
    source_id: str
    text_origin: TextOrigin
    issues: list[Issue] = field(default_factory=list)


def publication_time(raw: RawRecord) -> datetime:
    """Дата из ленты; если её нет или она из будущего (часы источника врут) — время сбора."""
    published = raw.published_at
    if published is None or published > raw.fetched_at + FUTURE_SLACK:
        return raw.fetched_at
    return published.astimezone(UTC)


def repaired(raw: RawRecord) -> tuple[RawRecord, list[Issue]]:
    """Запись с исправленной кодировкой заголовка, анонса, текста ленты и страницы."""
    title, *found = repair(raw.title)
    summary, *summary_issue = repair(raw.summary)
    feed_text, *feed_issue = repair(raw.feed_text)
    page, *page_issue = repair(raw.html)
    issues: list[Issue] = []
    for issue in [*found, *summary_issue, *feed_issue, *page_issue]:
        if issue is not None and issue not in issues:
            issues.append(issue)
    fixed = replace(raw, title=title or raw.title, summary=summary, feed_text=feed_text, html=page)
    return fixed, issues


def quality(raw: RawRecord, origin: TextOrigin, text: str) -> list[Issue]:
    issues: list[Issue] = []
    if raw.published_at is None:
        issues.append(Issue("no_date", "в ленте нет даты публикации, взято время сбора"))
    elif raw.published_at > raw.fetched_at + FUTURE_SLACK:
        issues.append(Issue("no_date", "дата публикации из будущего, взято время сбора"))
    if paywalled(raw.html):
        issues.append(
            Issue("paywall", f"платный доступ: в тексте только бесплатная часть ({origin})")
        )
    elif raw.html and origin in ("summary", "title"):
        issues.append(Issue("empty_page", f"на странице не найден текст, взят {origin} из ленты"))
    return issues


def normalize(raw: RawRecord, source: Source | None) -> Normalized:
    raw, issues = repaired(raw)
    title = clean(raw.title)
    paragraphs, origin = article_text(raw, source.text_selector if source else None)
    text = "\n\n".join(paragraphs)[:MAX_TEXT]
    issues.extend(quality(raw, origin, text))
    summary = strip_markup(raw.summary) or None
    if summary and paragraphs and (summary in paragraphs[0] or paragraphs[0] in summary):
        summary = None
    item = NewsItem(
        id="news:" + raw.raw_id.removeprefix("raw:"),
        kind=MaterialKind.NEWS,
        source=SourceRef(
            name=source.name if source else raw.source_id,
            channel=Channel.RSS,
            url=HttpUrl(source.url) if source else None,
        ),
        url=HttpUrl(raw.url),
        title=title,
        image=as_url(pick_image(raw.url, raw.image_url, raw.html, raw.feed_text)),
        published_at=publication_time(raw),
        collected_at=raw.fetched_at,
        language=language(f"{title} {text}"),
        text=text,
        summary=summary,
        fragments=[
            Fragment(id=f"p{index}", text=paragraph, locator=f"абзац {index}")
            for index, paragraph in enumerate(paragraphs[:MAX_FRAGMENTS], start=1)
        ],
        dedup=Dedup(content_hash=content_hash(title, text)),
    )
    return Normalized(
        item=item, raw_id=raw.raw_id, source_id=raw.source_id, text_origin=origin, issues=issues
    )


@dataclass
class NormalizeReport:
    normalized: int = 0
    by_origin: dict[str, int] = field(default_factory=dict)
    issues: dict[str, int] = field(default_factory=dict)
    failed: int = 0


async def ensure_schema(connection: Any) -> None:
    await ensure_issues(connection)
    await connection.execute(SCHEMA)


async def save(connection: Any, result: Normalized, now: datetime) -> None:
    item = result.item
    await connection.execute(
        UPSERT_SQL,
        item.id,
        result.raw_id,
        result.source_id,
        str(item.url),
        item.title,
        item.published_at,
        item.collected_at,
        item.language,
        item.dedup.content_hash,
        result.text_origin,
        item.model_dump_json(),
        now,
    )


async def normalize_pending(
    connection: Any,
    sources: Sequence[Source],
    *,
    everything: bool = False,
    limit: int = 5000,
    now: datetime | None = None,
) -> NormalizeReport:
    """Нормализует новые записи raw_news, а с everything — все заново: например, после того
    как у источника поменялся text_selector. Ошибка одной записи пишется в news_issues и не
    останавливает остальные; после трёх ошибок запись больше не берётся в работу."""
    await ensure_schema(connection)
    by_id = {source.id: source for source in sources}
    records = await connection.fetch(ALL_SQL if everything else PENDING_SQL, limit)
    report = NormalizeReport()
    moment = now or datetime.now(UTC)
    for record in records:
        try:
            raw = RawRecord.from_record(record)
            result = normalize(raw, by_id.get(raw.source_id))
            await save(connection, result, moment)
        except Exception as error:
            await record_issue(
                connection, STAGE, failure(error), raw_id=record["raw_id"], now=moment
            )
            report.failed += 1
            continue
        for issue in result.issues:
            await record_issue(
                connection, STAGE, issue, raw_id=raw.raw_id, news_id=result.item.id, now=moment
            )
            report.issues[issue.kind] = report.issues.get(issue.kind, 0) + 1
        report.normalized += 1
        origins = report.by_origin
        origins[result.text_origin] = origins.get(result.text_origin, 0) + 1
    return report


async def load_item(connection: Any, news_id: str) -> NewsItem | None:
    record = await connection.fetchrow("SELECT item FROM news_items WHERE news_id = $1", news_id)
    return NewsItem.model_validate_json(record["item"]) if record else None
