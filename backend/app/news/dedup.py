"""Дедупликация (A3-03): одно событие из пяти изданий — одна карточка-сюжет, ссылки на все
публикации сохраняются списком.

Без эмбеддингов (ADR 0002), в два шага:

1. Точная перепечатка — тот же content_hash (слова заголовка и текста без регистра
   и пунктуации).
2. То же событие — TF-IDF по основам слов заголовка (вдвое весомее) и первых двух абзацев.
   Основа — первые пять букв слова, числа берутся целиком: «0,05%» и «14%» отличают
   одно событие от похожего. Публикация присоединяется к сюжету, если она похожа на любую
   его публикацию не меньше чем на 0,45 и вышла не позже 72 часов после неё. Порог подобран
   на первом обходе: выше 0,45 — одно событие в разных изданиях, ниже — соседние темы.

Сюжет не пересобирается: новые публикации присоединяются к уже найденным сюжетам.
"""

import math
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from app.contracts.news import NewsItem, NewsStory, StoryItem, StoryMatch
from app.news.fetch import Source

THRESHOLD = 0.45
WINDOW = timedelta(hours=72)
CORPUS = timedelta(days=7)
STEM = 5
LEAD_FRAGMENTS = 2
WORD = re.compile(r"[a-zа-яё0-9]+(?:[.,][0-9]+)?%?")
STOPWORDS = frozenset(
    """и в во на с со по к ко о об от до из за для не ни что как это этот эта эти его ее её
    их при также а но или же бы то так все всё был была были было будет будут может могут
    года году год лет млн млрд трлн руб рублей тыс уже сообщил сообщила сообщили сообщает
    заявил заявила заявили по словам ранее после около более менее который которая которые
    the of and to in for on with""".split()
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS news_dedup (
    news_id text PRIMARY KEY,
    cluster_id text NOT NULL,
    duplicate_of text,
    match text NOT NULL,
    similarity double precision,
    assigned_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS news_dedup_cluster ON news_dedup (cluster_id);
"""
DOCS_SQL = """
SELECT n.news_id, n.source_id, n.item, d.cluster_id, d.duplicate_of
FROM news_items n LEFT JOIN news_dedup d ON d.news_id = n.news_id
WHERE n.published_at >= $1 OR d.news_id IS NULL
ORDER BY n.published_at, n.news_id
"""
SAVE_SQL = """
INSERT INTO news_dedup (news_id, cluster_id, duplicate_of, match, similarity, assigned_at)
VALUES ($1, $2, $3, $4, $5, $6)
ON CONFLICT (news_id) DO NOTHING
"""
ITEM_SQL = """
UPDATE news_items SET item = jsonb_set(jsonb_set(item, '{dedup,cluster_id}', to_jsonb($2::text)),
                                       '{dedup,duplicate_of}', coalesce(to_jsonb($3::text), 'null'))
WHERE news_id = $1
"""
STORY_SQL = """
SELECT n.source_id, n.item, d.match, d.similarity, d.duplicate_of
FROM news_dedup d JOIN news_items n ON n.news_id = d.news_id
WHERE d.cluster_id = $1
ORDER BY n.published_at, n.news_id
"""
RECENT_SQL = """
SELECT d.cluster_id FROM news_dedup d JOIN news_items n ON n.news_id = d.news_id
GROUP BY d.cluster_id
HAVING max(n.published_at) >= $1
ORDER BY max(n.published_at) DESC
LIMIT $2
"""


def stems(text: str) -> list[str]:
    result = []
    for word in WORD.findall(text.lower()):
        if word in STOPWORDS:
            continue
        if word[0].isdigit():
            result.append(word)
        elif len(word) >= 3:
            result.append(word[:STEM])
    return result


def terms(item: NewsItem) -> Counter[str]:
    lead = " ".join(fragment.text for fragment in item.fragments[:LEAD_FRAGMENTS]) or item.text
    return Counter(stems(item.title) * 2 + stems(lead))


def vectors(items: Sequence[NewsItem]) -> dict[str, dict[str, float]]:
    """TF-IDF с нормировкой. idf сглажен, как в scikit-learn: на маленьком окне общие слова
    не обнуляются, и две одинаковые новости остаются похожими."""
    counts = {item.id: terms(item) for item in items}
    frequency = Counter(term for bag in counts.values() for term in bag)
    total = len(items)
    result: dict[str, dict[str, float]] = {}
    for news_id, bag in counts.items():
        weights = {
            term: (1 + math.log(count)) * (math.log((1 + total) / (1 + frequency[term])) + 1)
            for term, count in bag.items()
        }
        norm = math.sqrt(sum(value * value for value in weights.values())) or 1.0
        result[news_id] = {term: value / norm for term, value in weights.items()}
    return result


def cosine(left: dict[str, float], right: dict[str, float]) -> float:
    if len(left) > len(right):
        left, right = right, left
    return sum(value * right.get(term, 0.0) for term, value in left.items())


@dataclass(frozen=True)
class Assignment:
    news_id: str
    cluster_id: str
    match: StoryMatch
    duplicate_of: str | None = None
    similarity: float | None = None


def cluster_id_for(news_id: str) -> str:
    return "story:" + news_id.removeprefix("news:")


def assign(
    items: Sequence[NewsItem], known: dict[str, Assignment] | None = None
) -> list[Assignment]:
    """Сюжеты для публикаций без сюжета. Публикации идут по времени: каждая присоединяется
    к перепечатке или к самой похожей публикации из окна, иначе открывает новый сюжет."""
    placed: dict[str, Assignment] = dict(known or {})
    ordered = sorted(items, key=lambda item: (item.published_at, item.id))
    weights = vectors(ordered)
    by_hash: dict[str, str] = {}
    result: list[Assignment] = []
    for index, item in enumerate(ordered):
        if item.id in placed:
            by_hash.setdefault(item.dedup.content_hash, item.id)
            continue
        original = by_hash.get(item.dedup.content_hash)
        if original is not None:
            assignment = Assignment(
                item.id, placed[original].cluster_id, StoryMatch.EXACT, original, 1.0
            )
        else:
            best: tuple[float, str] | None = None
            for earlier in ordered[:index]:
                if earlier.id not in placed or item.published_at - earlier.published_at > WINDOW:
                    continue
                score = cosine(weights[item.id], weights[earlier.id])
                if score >= THRESHOLD and (best is None or score > best[0]):
                    best = (score, earlier.id)
            if best is None:
                assignment = Assignment(item.id, cluster_id_for(item.id), StoryMatch.FIRST)
            else:
                assignment = Assignment(
                    item.id,
                    placed[best[1]].cluster_id,
                    StoryMatch.SIMILAR,
                    similarity=round(min(best[0], 1.0), 4),
                )
        placed[item.id] = assignment
        by_hash.setdefault(item.dedup.content_hash, item.id)
        result.append(assignment)
    return result


def representative(items: Sequence[NewsItem], official: set[str]) -> NewsItem:
    """Карточка сюжета: официальный источник (ЦБ, министерство), иначе первая публикация,
    при равенстве — где текст полнее."""
    return min(
        items,
        key=lambda item: (item.source.name not in official, item.published_at, -len(item.text)),
    )


def story(
    cluster_id: str,
    members: Sequence[tuple[NewsItem, Assignment]],
    official: Iterable[str] = (),
) -> NewsStory:
    items = [item for item, _ in members]
    main = representative(items, set(official))
    return NewsStory(
        id=cluster_id,
        representative_id=main.id,
        title=main.title,
        first_published_at=min(item.published_at for item in items),
        last_published_at=max(item.published_at for item in items),
        items=[
            StoryItem(
                news_id=item.id,
                source=item.source.name,
                url=item.url,
                title=item.title,
                published_at=item.published_at,
                match=assignment.match,
                similarity=assignment.similarity,
                duplicate_of=assignment.duplicate_of,
            )
            for item, assignment in members
        ],
    )


@dataclass
class DedupReport:
    assigned: int = 0
    exact: int = 0
    similar: int = 0
    stories: int = 0


async def ensure_schema(connection: Any) -> None:
    await connection.execute(SCHEMA)


async def dedup_pending(connection: Any, now: datetime | None = None) -> DedupReport:
    """Сюжеты для новых публикаций: сравниваем с публикациями последних семи дней."""
    await ensure_schema(connection)
    moment = now or datetime.now(UTC)
    records = await connection.fetch(DOCS_SQL, moment - CORPUS)
    items = [NewsItem.model_validate_json(record["item"]) for record in records]
    known = {
        record["news_id"]: Assignment(
            record["news_id"], record["cluster_id"], StoryMatch.FIRST, record["duplicate_of"]
        )
        for record in records
        if record["cluster_id"] is not None
    }
    fresh = assign(items, known)
    for assignment in fresh:
        await connection.execute(
            SAVE_SQL,
            assignment.news_id,
            assignment.cluster_id,
            assignment.duplicate_of,
            assignment.match.value,
            assignment.similarity,
            moment,
        )
        await connection.execute(
            ITEM_SQL, assignment.news_id, assignment.cluster_id, assignment.duplicate_of
        )
    return DedupReport(
        assigned=len(fresh),
        exact=sum(item.match is StoryMatch.EXACT for item in fresh),
        similar=sum(item.match is StoryMatch.SIMILAR for item in fresh),
        stories=sum(item.match is StoryMatch.FIRST for item in fresh),
    )


def official_names(sources: Sequence[Source]) -> set[str]:
    return {source.name for source in sources if source.kind == "regulator"}


async def load_story(
    connection: Any, cluster_id: str, sources: Sequence[Source] = ()
) -> NewsStory | None:
    records = await connection.fetch(STORY_SQL, cluster_id)
    if not records:
        return None
    members = [
        (
            NewsItem.model_validate_json(record["item"]),
            Assignment(
                "",
                cluster_id,
                StoryMatch(record["match"]),
                record["duplicate_of"],
                record["similarity"],
            ),
        )
        for record in records
    ]
    return story(cluster_id, members, official_names(sources))


async def recent_stories(
    connection: Any, since: datetime, sources: Sequence[Source] = (), limit: int = 100
) -> list[NewsStory]:
    """Сюжеты, в которых были публикации после since: свежие сверху."""
    found = []
    for record in await connection.fetch(RECENT_SQL, since, limit):
        loaded = await load_story(connection, record["cluster_id"], sources)
        if loaded is not None:
            found.append(loaded)
    return found
