"""A5-16: связи между новостями по общим сущностям и теме, без вызовов модели.

Публикации одного сюжета (A3-04) склеиваются в одну историю. Две истории связаны, если у них
есть общий участник, отрасль или регион (ключи A3-11) и общая тема. Вид связи:

- «Противоречит» — те же участники или контекст и тема, оценка для бизнеса противоположная,
  а между публикациями не больше трёх дней: источники расходятся в оценке одного события;
- «Продолжение» — общий участник (компания или персона): развитие того же сюжета; разворот
  оценки спустя больше трёх дней — это тоже продолжение, ситуация изменилась;
- «Усиливает» — участников общих нет, но та же тема, отрасль или регион и та же направленность:
  ещё одно событие в пользу той же тенденции.

Оценка берётся из классификации новости. Это эвристика: связь показывает, что общего, и не
утверждает, что одна новость опровергает другую. Сюжет — связная группа таких историй.
"""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

from app.contracts.news import MaterialKind, NewsItem, Topic
from app.contracts.news_links import LinkKind, NewsLink, NewsLinks, NewsThread, NewsThreads
from app.news.cards import card
from app.news.entities import RELATED_WINDOW, Entity, entities_of
from app.rank.rules import TOPIC_TITLES

STRONG = frozenset({"company", "person"})
CONTRADICTION_WINDOW = timedelta(days=3)
LABELS = {
    LinkKind.CONTINUATION: "Продолжение",
    LinkKind.REINFORCES: "Усиливает",
    LinkKind.CONTRADICTS: "Противоречит",
}
POLARITY = {"positive": 1, "negative": -1}
TONE = {1: "позитивно", -1: "негативно"}
MAX_NAMES = 3


@dataclass
class Story:
    key: str
    item: NewsItem
    start: datetime
    entities: dict[str, Entity] = field(default_factory=dict)
    topics: set[Topic] = field(default_factory=set)

    @property
    def polarity(self) -> int | None:
        classification = self.item.classification
        return POLARITY.get(classification.sentiment or "") if classification else None


def stories(items: Sequence[NewsItem]) -> list[Story]:
    groups: dict[str, list[NewsItem]] = {}
    for item in items:
        if item.kind is MaterialKind.NEWS:
            groups.setdefault(item.dedup.cluster_id or item.id, []).append(item)
    result = []
    for key, members in groups.items():
        latest = max(members, key=lambda n: (n.published_at, n.id))
        story = Story(key, latest, min(n.published_at for n in members))
        for member in members:
            for found in entities_of(member):
                story.entities.setdefault(found.key, found)
            if member.classification:
                story.topics |= set(member.classification.topics) - {Topic.OTHER}
        result.append(story)
    return sorted(result, key=lambda s: (s.start, s.item.id))


def names(entities: Sequence[Entity]) -> str:
    return ", ".join(dict.fromkeys(e.name for e in entities[:MAX_NAMES]))


def link(older: Story, newer: Story) -> NewsLink | None:
    gap = newer.start - older.start
    if gap < timedelta(0) or gap > RELATED_WINDOW:
        return None
    shared = [e for key, e in newer.entities.items() if key in older.entities and e.kind != "topic"]
    topics = sorted(older.topics & newer.topics)
    if not shared or not topics:
        return None
    strong = [e for e in shared if e.kind in STRONG]
    before, after = older.polarity, newer.polarity
    opposite = before is not None and after is not None and before != after
    if opposite and gap <= CONTRADICTION_WINDOW and (strong or len(shared) >= 2):
        kind = LinkKind.CONTRADICTS
    elif strong:
        kind = LinkKind.CONTINUATION
    elif before is not None and before == after:
        kind = LinkKind.REINFORCES
    else:
        return None
    subject = names(strong or shared)
    topic = TOPIC_TITLES[topics[0]]
    date = older.start.strftime("%d.%m")
    if kind is LinkKind.CONTRADICTS:
        explanation = (
            f"Расходится с «{older.item.title}» ({date}): тема «{topic}», {subject}; "
            f"там событие оценено {TONE[before or 1]}, здесь — {TONE[after or 1]}"
        )
    elif kind is LinkKind.CONTINUATION:
        turn = (
            f"; оценка сменилась: было {TONE[before]}, стало {TONE[after]}"
            if opposite and before is not None and after is not None
            else ""
        )
        explanation = (
            f"Продолжение сюжета о {subject}: тема «{topic}», "
            f"раньше — «{older.item.title}» ({date}){turn}"
        )
    else:
        explanation = (
            f"Ещё одно событие в ту же сторону: тема «{topic}», {subject}; "
            f"«{older.item.title}» ({date}) тоже {TONE[after or 1]} для бизнеса"
        )
    confidence: Literal["high", "medium"] = "high" if strong else "medium"
    return NewsLink(
        news_id=newer.item.id,
        linked_id=older.item.id,
        kind=kind,
        label=LABELS[kind],
        explanation=explanation,
        shared=list(dict.fromkeys(e.name for e in shared)),
        shared_keys=[e.key for e in shared],
        topics=topics,
        strength=round(sum(e.weight for e in shared), 3),
        confidence=confidence,
    )


def all_links(found: Sequence[Story]) -> list[NewsLink]:
    result = []
    for index, newer in enumerate(found):
        for older in found[:index]:
            candidate = link(older, newer)
            if candidate is not None:
                result.append(candidate)
    return result


def threads(
    items: Sequence[NewsItem],
    *,
    now: datetime,
    days: int = 7,
    limit: int = 20,
    shared: set[str] | None = None,
) -> NewsThreads:
    since = now - timedelta(days=days)
    found = [s for s in stories(items) if since <= s.item.published_at <= now]
    links = all_links(found)
    parent = {s.item.id: s.item.id for s in found}

    def root(news_id: str) -> str:
        while parent[news_id] != news_id:
            parent[news_id] = parent[parent[news_id]]
            news_id = parent[news_id]
        return news_id

    for item in links:
        parent[root(item.news_id)] = root(item.linked_id)
    by_id = {s.item.id: s for s in found}
    groups: dict[str, list[Story]] = {}
    for story in found:
        groups.setdefault(root(story.item.id), []).append(story)
    result = []
    for members in groups.values():
        ids = {s.item.id for s in members}
        inner = [item for item in links if item.news_id in ids]
        if len(members) < 2 or not inner:
            continue
        common = Counter(name for item in inner for name in item.shared)
        topics = Counter(topic for item in inner for topic in item.topics)
        lead = common.most_common(1)[0][0]
        topic = topics.most_common(1)[0][0]
        ordered = sorted(members, key=lambda s: (s.start, s.item.id))
        result.append(
            NewsThread(
                id=f"thread:{ordered[0].item.id}",
                title=f"{lead}: {TOPIC_TITLES[topic]}",
                news=[card(by_id[s.item.id].item, shared or ()) for s in ordered],
                entities=[name for name, _ in common.most_common(5)],
                topics=[t for t, _ in topics.most_common()],
                links=inner,
            )
        )
    result.sort(key=lambda t: (-max(c.published_at.timestamp() for c in t.news), t.id))
    return NewsThreads(
        generated_at=now,
        period_days=days,
        threads=result[:limit],
        considered=len(found),
    )


def links_for(
    news_id: str, items: Sequence[NewsItem], *, shared: set[str] | None = None
) -> NewsLinks | None:
    target = next((n for n in items if n.id == news_id and n.kind is MaterialKind.NEWS), None)
    if target is None:
        return None
    found = stories(items)
    own = next(s for s in found if s.key == (target.dedup.cluster_id or target.id))
    result = []
    for other in found:
        if other is own:
            continue
        older, newer = (other, own) if other.start <= own.start else (own, other)
        candidate = link(older, newer)
        if candidate is not None:
            result.append(candidate)
    result.sort(key=lambda item: (-item.strength, item.news_id, item.linked_id))
    linked = {item.linked_id if item.news_id == own.item.id else item.news_id for item in result}
    return NewsLinks(
        news_id=own.item.id,
        links=result,
        cards=[card(s.item, shared or ()) for s in found if s.item.id in linked],
    )
