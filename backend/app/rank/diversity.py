"""Разнообразие ленты (A4-05): не больше двух материалов об одном событии подряд и в первых
карточках — и риск, и возможность, если такие материалы есть.

Баллы не меняются, меняется только порядок: лента остаётся объяснимой. Событие узнаём по
главной теме и главному участнику, сегменту или региону: публикации одного сюжета (A3-03)
лента уже склеила, а пересказы, которые дедупликация не узнала, ловит этот ключ. Знак
события — по тональности классификации для бизнеса: negative — риск, positive —
возможность, остальное нейтрально. Поднимаем ради баланса только материал с достаточным
баллом: подмешать нерелевантное ради разнообразия хуже, чем однобокая лента.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field

from app.contracts.feed import Polarity
from app.contracts.news import NewsItem, Topic
from app.core.words import plural

MAX_RUN = 2
WINDOW = 5
MIN_SCORE = 0.35
POLARITY = {
    "negative": Polarity.RISK,
    "positive": Polarity.OPPORTUNITY,
}
TITLES = {Polarity.RISK: "добавлен риск", Polarity.OPPORTUNITY: "добавлена возможность"}


@dataclass(frozen=True)
class Entry:
    id: str
    score: float
    event: str
    polarity: Polarity
    title: str = ""


@dataclass
class Diversified:
    order: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def polarity(news: NewsItem) -> Polarity:
    sentiment = news.classification.sentiment if news.classification else None
    return POLARITY.get(sentiment or "", Polarity.NEUTRAL)


def event_key(news: NewsItem) -> str:
    """Ключ события: главная тема и главный участник, сегмент или регион. Сюжеты в ленте уже
    склеены, повторы — это пересказы, которые дедупликация не узнала; без разбора — сюжет."""
    classification = news.classification
    if classification is None:
        return f"story:{news.dedup.cluster_id or news.id}"
    topic = classification.topics[0] if classification.topics else Topic.OTHER
    anchors = [*classification.entities, *classification.segments, *classification.regions]
    anchor = anchors[0].lower().replace("ё", "е").strip() if anchors else ""
    return f"{topic.value}:{anchor}"


def entry(news: NewsItem, score: float) -> Entry:
    return Entry(news.id, score, event_key(news), polarity(news), news.title)


def repeats(order: Sequence[Entry], candidate: Entry, max_run: int) -> bool:
    tail = order[-max_run:]
    return len(tail) == max_run and all(item.event == candidate.event for item in tail)


def missing(order: Sequence[Entry], pending: Sequence[Entry], min_score: float) -> set[Polarity]:
    present = {item.polarity for item in order}
    available = {item.polarity for item in pending if item.score >= min_score}
    return {wanted for wanted in TITLES if wanted not in present and wanted in available}


def diversify(
    entries: Sequence[Entry],
    max_run: int = MAX_RUN,
    window: int = WINDOW,
    min_score: float = MIN_SCORE,
) -> Diversified:
    """Исходный порядок ленты с двумя поправками: третий подряд материал об одном событии
    уступает место следующему, а последние места первого окна отдаются недостающему знаку."""
    pending = list(entries)
    order: list[Entry] = []
    notes: list[str] = []
    while pending:
        allowed = [item for item in pending if not repeats(order, item, max_run)] or pending
        choice = allowed[0]
        blocked = choice is not pending[0]
        slots = window - len(order)
        need = missing(order, pending, min_score) if slots > 0 else set()
        if need and slots <= len(need) and choice.polarity not in need:
            promoted = next(
                (item for item in allowed if item.polarity in need and item.score >= min_score),
                None,
            )
            if promoted is not None:
                choice = promoted
                notes.append(
                    f"«{choice.title or choice.id}» поднят в первые {window}: "
                    f"для баланса {TITLES[choice.polarity]}"
                )
        if blocked and choice is allowed[0]:
            notes.append(
                f"«{pending[0].title or pending[0].id}» сдвинут ниже: "
                f"{max_run} {plural(max_run, 'материал', 'материала', 'материалов')} подряд "
                "уже об этом событии"
            )
        order.append(choice)
        pending.remove(choice)
    return Diversified([item.id for item in order], list(dict.fromkeys(notes)))
