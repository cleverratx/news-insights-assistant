"""Поиск по аналитике банка и новостям (A3-07): BM25 по фрагментам, затем переранжирование
моделью. Ответ всегда с дословной цитатой и ссылкой на документ и место в нём — RAG без
потери связи с источником (ADR 0002: без эмбеддингов).

Единица поиска — фрагмент (абзац новости, абзац аналитики «стр. N, абзац M»), а не документ:
так цитата точная, а ссылка ведёт к месту. Слова приводятся к основе из первых пяти букв,
числа берутся целиком. Цитата — предложение фрагмента, в котором больше всего слов запроса;
если переранжировала модель, берём её цитату, но только если она дословно есть во фрагменте.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from rank_bm25 import BM25Okapi

from app.contracts.news import MaterialKind, NewsItem
from app.contracts.search import SearchHit

STEM = 5
MAX_QUOTE = 400
WORD = re.compile(r"[a-zа-яё0-9]+(?:[.,][0-9]+)?%?")
SENTENCE = re.compile(r"(?<=[.!?…])\s+(?=[А-ЯЁA-Z«\"])")
STOPWORDS = frozenset(
    """и в во на с со по к ко о об от до из за для не ни что как это этот эта эти его ее её
    их при также а но или же бы то так все всё был была были было будет будут может могут
    года году год лет млн млрд трлн руб рублей тыс уже который которая которые чем чтобы
    the of and to in for on with""".split()
)


def tokens(text: str) -> list[str]:
    result = []
    for word in WORD.findall(text.lower()):
        if word in STOPWORDS:
            continue
        if word[0].isdigit():
            result.append(word)
        elif len(word) >= 2:
            result.append(word[:STEM])
    return result


@dataclass(frozen=True)
class Passage:
    item: NewsItem
    fragment_id: str
    locator: str | None
    text: str

    @property
    def key(self) -> str:
        return f"{self.item.id}#{self.fragment_id}"


def passages(items: Sequence[NewsItem]) -> list[Passage]:
    """Фрагменты материалов. Заголовок добавляется к тексту фрагмента для поиска: запрос
    «ставка ЦБ» должен находить абзац обзора о ставке, даже если в абзаце сказано «регулятор»."""
    found: list[Passage] = []
    for item in items:
        fragments = item.fragments or []
        if not fragments:
            found.append(Passage(item, "text", None, item.text))
        for fragment in fragments:
            found.append(Passage(item, fragment.id, fragment.locator, fragment.text))
    return found


def best_sentence(text: str, query: Sequence[str]) -> str:
    """Предложение фрагмента, где больше всего слов запроса: это и есть цитата."""
    wanted = set(query)
    sentences = [sentence.strip() for sentence in SENTENCE.split(text) if sentence.strip()]
    if not sentences:
        return text[:MAX_QUOTE]
    best = max(sentences, key=lambda sentence: len(wanted & set(tokens(sentence))))
    return best[:MAX_QUOTE]


def normalized(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip().lower()


def verbatim(quote: str, text: str) -> bool:
    return bool(quote.strip()) and normalized(quote) in normalized(text)


def document_ref(passage: Passage) -> str:
    """Где искать цитату, с датой материала: старый прогноз банка не выдаёт себя за текущий."""
    where = passage.locator or "текст"
    day = f"{passage.item.published_at:%d.%m.%Y}"
    if passage.item.kind is MaterialKind.BANK_ANALYTICS:
        return f"{passage.item.title} ({day}), {where}"
    return f"{passage.item.source.name}: {passage.item.title} ({day}), {where}"


def hit(passage: Passage, quote: str, score: float, bm25: float, rerank: float | None) -> SearchHit:
    return SearchHit(
        doc_id=passage.item.id,
        kind=passage.item.kind,
        title=passage.item.title,
        source=passage.item.source.name,
        url=passage.item.url,
        document=document_ref(passage),
        published_at=passage.item.published_at,
        fragment_id=passage.fragment_id,
        locator=passage.locator,
        quote=quote,
        score=round(min(max(score, 0.0), 1.0), 4),
        bm25=round(bm25, 4),
        rerank=rerank,
    )


@dataclass(frozen=True)
class Candidate:
    passage: Passage
    bm25: float


class Index:
    """BM25 по фрагментам. Строится на каждый поиск: материалов сотни, это быстрее кеша."""

    def __init__(self, found: Sequence[Passage]) -> None:
        self.passages = list(found)
        documents = [tokens(f"{passage.item.title} {passage.text}") for passage in self.passages]
        self.vocabulary = [set(document) for document in documents]
        self.bm25 = BM25Okapi(documents) if documents else None

    def search(
        self,
        query: str,
        limit: int = 20,
        kinds: set[MaterialKind] | None = None,
        since: datetime | None = None,
    ) -> list[Candidate]:
        """BM25, умноженный на долю слов запроса во фрагменте: подпись графика, где десять
        раз повторено «цены», не обгонит абзац, где есть и «цены», и «золото»."""
        terms = tokens(query)
        if self.bm25 is None or not terms:
            return []
        wanted = set(terms)
        raw = self.bm25.get_scores(terms)
        scores = [
            float(score) * len(wanted & words) / len(wanted)
            for score, words in zip(raw, self.vocabulary, strict=True)
        ]
        ranked = sorted(range(len(self.passages)), key=lambda index: -scores[index])
        result: list[Candidate] = []
        for index in ranked:
            passage = self.passages[index]
            if scores[index] <= 0:
                break
            if kinds and passage.item.kind not in kinds:
                continue
            if since and passage.item.published_at < since:
                continue
            result.append(Candidate(passage, scores[index]))
            if len(result) >= limit:
                break
        return result


def bm25_hits(query: str, candidates: Sequence[Candidate], limit: int) -> list[SearchHit]:
    """Ответ без модели: порядок BM25, оценка — доля от лучшего результата."""
    top = candidates[0].bm25 if candidates else 1.0
    terms = tokens(query)
    return [
        hit(
            candidate.passage,
            best_sentence(candidate.passage.text, terms),
            candidate.bm25 / top,
            candidate.bm25,
            None,
        )
        for candidate in candidates[:limit]
    ]
