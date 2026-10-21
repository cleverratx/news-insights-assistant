import json
from datetime import UTC, datetime, timedelta

import asyncpg

from app.contracts.news import Classification, Marker, MaterialKind, Topic
from app.news.classify import classify, rule_classify, system_prompt
from app.news.markers import (
    TITLES,
    counts,
    feed,
    filter_items,
    from_topics,
    known,
    markers_of,
    matches,
    with_markers,
)
from app.search.engine import Index, passages
from app.search.service import search
from tests.test_data_loader import database
from tests.test_news_classify import GOOD, FakeLLM, news

__all__ = ["database"]

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)


def classified(title: str, text: str, topics: list[Topic], markers: list[Marker] | None = None):  # type: ignore[no-untyped-def]
    item = news(title, text)
    return item.model_copy(
        update={
            "id": f"news:{abs(hash(title))}",
            "classification": Classification(topics=topics, markers=markers or []),
        }
    )


def test_closed_list_in_prompt() -> None:
    prompt = system_prompt()
    assert len(TITLES) == 10
    for marker in Marker:
        assert f"- {marker.value}: {TITLES[marker]}" in prompt
    assert "других не придумывай" in prompt


def test_unknown_markers_are_dropped() -> None:
    assert known(["fx", "weather", "fx", "logistics", "labor", "incident"]) == [
        Marker.FX,
        Marker.LOGISTICS,
        Marker.LABOR,
    ]
    assert known([]) == []


def test_markers_from_topics_and_words() -> None:
    assert from_topics([Topic.KEY_RATE, Topic.INFLATION, Topic.FX]) == [
        Marker.ECONOMY_RATE,
        Marker.FX,
    ]
    fire = from_topics([Topic.INDUSTRY], "На НПЗ в Татарстане произошёл пожар")
    assert fire == [Marker.INCIDENT, Marker.INDUSTRY_EVENT]
    tech = from_topics([], "Банки внедряют искусственный интеллект в кредитование")
    assert tech == [Marker.TECHNOLOGY]
    assert from_topics([], "Сбор урожая завершён") == []


async def test_model_markers_are_checked() -> None:
    answer = GOOD | {"markers": ["trade_duties", "weather", "commodities_prices"]}
    outcome = await classify(FakeLLM(json.dumps(answer)), news())
    assert outcome.classification.markers == [Marker.TRADE_DUTIES, Marker.COMMODITIES_PRICES]
    only_unknown = GOOD | {"markers": ["weather"]}
    derived = await classify(FakeLLM(json.dumps(only_unknown)), news())
    assert derived.classification.markers == [Marker.COMMODITIES_PRICES, Marker.TRADE_DUTIES]


def test_rules_give_markers() -> None:
    found = rule_classify(news("Экспорт угля через порты вырос", "Отгрузки выросли."))
    assert Marker.LOGISTICS in found.markers and Marker.TRADE_DUTIES in found.markers


def test_filters_and_counts() -> None:
    rate = classified("ЦБ сохранил ставку", "Ставка 14%.", [Topic.KEY_RATE])
    fire = classified("Пожар на складе", "Сгорел склад.", [Topic.COMPANY], [Marker.INCIDENT])
    old = classified("Без разбора", "Текст.", [Topic.OTHER])
    old = old.model_copy(update={"classification": None})
    assert markers_of(rate) == [Marker.ECONOMY_RATE]
    assert markers_of(old) == []
    assert matches(old, [])
    assert filter_items([rate, fire, old], [Marker.INCIDENT]) == [fire]
    assert filter_items([rate, fire], [Marker.FX]) == []
    assert counts([rate, fire])[Marker.INCIDENT] == 1
    assert counts([rate, fire])[Marker.LABOR] == 0
    assert rate.classification is not None
    assert with_markers(rate.classification, rate).markers == [Marker.ECONOMY_RATE]


async def test_feed_and_search_filter(database: asyncpg.Connection) -> None:
    rate = classified("ЦБ сохранил ставку", "ЦБ сохранил ключевую ставку 14%.", [Topic.KEY_RATE])
    fire = classified(
        "Пожар на складе маркетплейса",
        "На складе маркетплейса произошёл пожар, ставка страховщиков вырастет.",
        [Topic.COMPANY],
        [Marker.INCIDENT],
    )
    await database.execute(
        "CREATE TABLE news_items (news_id text, published_at timestamptz, item jsonb)"
    )
    for index, item in enumerate((rate, fire)):
        await database.execute(
            "INSERT INTO news_items VALUES ($1, $2, $3::jsonb)",
            item.id,
            NOW - timedelta(hours=index),
            item.model_dump_json(),
        )
    found = await feed(database, NOW - timedelta(days=1), [Marker.INCIDENT])
    assert [item.id for item in found] == [fire.id]
    assert len(await feed(database, NOW - timedelta(days=1))) == 2
    result = await search(
        database, "ставка", kinds=[MaterialKind.NEWS], markers=[Marker.INCIDENT], now=NOW
    )
    assert {hit.doc_id for hit in result.hits} == {fire.id}
    assert len(Index(passages([rate, fire])).search("ставка")) == 2
