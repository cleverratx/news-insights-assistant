"""Маркеры новости (A3-10): короткая метка для фильтров ленты — «Валюта», «Логистика»,
«Происшествие». Список из десяти маркеров зафиксирован заранее, модель выбирает из него
и свои не придумывает: неизвестное значение отбрасывается.

Маркеры ставит быстрая модель в том же вызове, что и классификацию (A3-04), — без лишнего
расхода бюджета. Если модель недоступна или маркеров не дала, они выводятся из тем
классификации и словаря: это же покрывает новости, разобранные до появления маркеров.
"""

import re
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

from app.contracts.news import Classification, Marker, NewsItem, Topic

MAX_MARKERS = 3

TITLES: dict[Marker, str] = {
    Marker.ECONOMY_RATE: "Экономика и ставка",
    Marker.FX: "Валюта",
    Marker.REGULATION_TAXES: "Регулирование и налоги",
    Marker.TRADE_DUTIES: "ВЭД и пошлины",
    Marker.COMMODITIES_PRICES: "Сырьё и цены",
    Marker.LOGISTICS: "Логистика",
    Marker.LABOR: "Рынок труда",
    Marker.TECHNOLOGY: "Технологии",
    Marker.INDUSTRY_EVENT: "Отраслевое событие",
    Marker.INCIDENT: "Происшествие",
}
HINTS: dict[Marker, str] = {
    Marker.ECONOMY_RATE: "ключевая ставка, инфляция, ВВП, бюджет, кредиты и облигации",
    Marker.FX: "курс рубля и других валют, валютный контроль",
    Marker.REGULATION_TAXES: "законы, требования регуляторов, налоги и сборы, санкции",
    Marker.TRADE_DUTIES: "экспорт, импорт, пошлины, квоты, торговые соглашения",
    Marker.COMMODITIES_PRICES: "сырьё и цены на него: нефть, газ, металлы, зерно, удобрения",
    Marker.LOGISTICS: "перевозки, тарифы РЖД, порты, маршруты и сроки доставки",
    Marker.LABOR: "зарплаты, кадры, занятость, мигранты",
    Marker.TECHNOLOGY: "технологии, ИИ, цифровизация, импортозамещение оборудования и ПО",
    Marker.INDUSTRY_EVENT: "событие отрасли или компании: сделка, запуск, отчётность",
    Marker.INCIDENT: "авария, пожар, сбой, кибератака, забастовка, стихийное бедствие",
}
FROM_TOPICS: dict[Topic, Marker] = {
    Topic.KEY_RATE: Marker.ECONOMY_RATE,
    Topic.INFLATION: Marker.ECONOMY_RATE,
    Topic.MACRO: Marker.ECONOMY_RATE,
    Topic.DEBT_MARKET: Marker.ECONOMY_RATE,
    Topic.CONSUMPTION: Marker.ECONOMY_RATE,
    Topic.FX: Marker.FX,
    Topic.REGULATION: Marker.REGULATION_TAXES,
    Topic.TAXES: Marker.REGULATION_TAXES,
    Topic.SANCTIONS: Marker.REGULATION_TAXES,
    Topic.TRADE: Marker.TRADE_DUTIES,
    Topic.COMMODITIES: Marker.COMMODITIES_PRICES,
    Topic.LOGISTICS: Marker.LOGISTICS,
    Topic.LABOR: Marker.LABOR,
    Topic.INDUSTRY: Marker.INDUSTRY_EVENT,
    Topic.COMPANY: Marker.INDUSTRY_EVENT,
    Topic.REGIONAL: Marker.INDUSTRY_EVENT,
}
WORDS: dict[Marker, re.Pattern[str]] = {
    Marker.INCIDENT: re.compile(
        r"\b(авари|пожар|взрыв|возгоран|утечк|разлив|обрушен|крушен|сбо[йяе]\b|кибератак|"
        r"хакерск|забастовк|наводнен|землетрясен|эвакуац|чрезвычайн)",
        re.IGNORECASE,
    ),
    Marker.TECHNOLOGY: re.compile(
        r"\b(искусственн\w* интеллект|нейросет|цифровиз|технолог|роботиз|беспилот|"
        r"импортозамещени\w* (?:по|оборудования)|программн\w* обеспечени|ит-)",
        re.IGNORECASE,
    ),
}


def title(marker: Marker) -> str:
    return TITLES[marker]


def prompt_list() -> str:
    return "\n".join(f"- {marker.value}: {TITLES[marker]} — {HINTS[marker]}" for marker in Marker)


def known(values: Iterable[object]) -> list[Marker]:
    """Только маркеры из списка, без повторов, не больше трёх."""
    result: list[Marker] = []
    for value in values:
        try:
            marker = Marker(str(value))
        except ValueError:
            continue
        if marker not in result:
            result.append(marker)
    return result[:MAX_MARKERS]


def from_topics(topics: Sequence[Topic], text: str = "") -> list[Marker]:
    """Маркеры без модели: по темам классификации и словарю для того, чего нет в темах —
    происшествий и технологий."""
    found = [FROM_TOPICS[topic] for topic in topics if topic in FROM_TOPICS]
    found += [marker for marker, pattern in WORDS.items() if pattern.search(f" {text} ")]
    if Marker.INCIDENT in found:
        found.remove(Marker.INCIDENT)
        found.insert(0, Marker.INCIDENT)
    return known(found)


def markers_of(item: NewsItem) -> list[Marker]:
    """Маркеры материала: от модели, а если их нет — выведенные из тем и текста."""
    classification = item.classification
    if classification is None:
        return []
    if classification.markers:
        return list(classification.markers)
    return from_topics(classification.topics, f"{item.title} {item.text[:3000]}")


def with_markers(classification: Classification, item: NewsItem) -> Classification:
    if classification.markers:
        return classification
    text = f"{item.title} {item.text[:3000]}"
    return classification.model_copy(update={"markers": from_topics(classification.topics, text)})


def matches(item: NewsItem, wanted: Iterable[Marker]) -> bool:
    """Фильтр ленты: материал подходит, если у него есть хотя бы один из выбранных маркеров.
    Пустой выбор — без фильтра."""
    selected = set(wanted)
    return not selected or bool(selected & set(markers_of(item)))


def filter_items(items: Sequence[NewsItem], wanted: Iterable[Marker]) -> list[NewsItem]:
    selected = list(wanted)
    return [item for item in items if matches(item, selected)]


FEED_SQL = """
SELECT item FROM news_items
WHERE published_at >= $1
ORDER BY published_at DESC
LIMIT $2
"""


async def feed(
    connection: Any, since: datetime, wanted: Iterable[Marker] = (), limit: int = 200
) -> list[NewsItem]:
    """Свежие новости с фильтром по маркерам: свежие сверху."""
    records = await connection.fetch(FEED_SQL, since, limit)
    items = [NewsItem.model_validate_json(record["item"]) for record in records]
    return filter_items(items, wanted)


def counts(items: Sequence[NewsItem]) -> dict[Marker, int]:
    """Сколько материалов под каждым маркером: для счётчиков у фильтров в интерфейсе."""
    result = dict.fromkeys(Marker, 0)
    for item in items:
        for marker in markers_of(item):
            result[marker] += 1
    return result
