from datetime import timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.agent.threads import links_for, threads
from app.api import news_links as endpoint
from app.api.companies import get_connection
from app.contracts.news import MaterialKind, NewsItem, Topic
from app.contracts.news_links import LinkKind, NewsThreads
from app.main import create_app
from tests.test_api_feed import material
from tests.test_news_classify import NOW

DAY = timedelta(days=1)


def news(id_: str, title: str, age: timedelta, sentiment: str, **classification: Any) -> NewsItem:
    return material(id_, title, age=age, sentiment=sentiment, **classification)


STEEL_UP = news(
    "news:steel-1",
    "Северсталь нарастила выпуск",
    5 * DAY,
    "positive",
    entities=["Северсталь"],
    topics=[Topic.COMMODITIES],
)
STEEL_DOWN = news(
    "news:steel-2",
    "Северсталь сократила выпуск",
    0 * DAY,
    "negative",
    entities=["Северсталь"],
    topics=[Topic.COMMODITIES],
)
RATE_SOFT = news(
    "news:rate-1",
    "ЦБ даст сигнал к снижению ставки",
    DAY,
    "positive",
    entities=["ЦБ РФ"],
    topics=[Topic.KEY_RATE],
)
RATE_HARD = news(
    "news:rate-2",
    "Банк России ужесточит риторику",
    0 * DAY,
    "negative",
    entities=["Банк России"],
    topics=[Topic.KEY_RATE],
)
COAL_A = news(
    "news:coal-1",
    "Угольщики Кузбасса сокращают добычу",
    2 * DAY,
    "negative",
    segments=["mining_metals"],
    regions=["Кемеровская область"],
    topics=[Topic.COMMODITIES],
)
COAL_B = news(
    "news:coal-2",
    "Экспорт угля из Кузбасса падает",
    0 * DAY,
    "negative",
    segments=["mining_metals"],
    regions=["Кемеровская область"],
    topics=[Topic.COMMODITIES],
)
ALL = [STEEL_UP, STEEL_DOWN, RATE_SOFT, RATE_HARD, COAL_A, COAL_B]


def kinds(items: list[NewsItem]) -> dict[tuple[str, str], LinkKind]:
    result = threads(items, now=NOW, days=7)
    return {(link.news_id, link.linked_id): link.kind for t in result.threads for link in t.links}


def test_continuation_contradiction_and_reinforcement() -> None:
    found = kinds(ALL)
    assert found[("news:steel-2", "news:steel-1")] is LinkKind.CONTINUATION
    assert found[("news:rate-2", "news:rate-1")] is LinkKind.CONTRADICTS
    assert found[("news:coal-2", "news:coal-1")] is LinkKind.REINFORCES
    assert len(found) == 3


def test_links_explain_what_is_shared() -> None:
    result = threads(ALL, now=NOW, days=7)
    by_kind = {link.kind: link for t in result.threads for link in t.links}
    steel = by_kind[LinkKind.CONTINUATION]
    assert steel.label == "Продолжение" and steel.confidence == "high"
    assert "Северсталь" in steel.explanation and "оценка сменилась" in steel.explanation
    assert steel.shared_keys == ["company:северсталь"]
    rate = by_kind[LinkKind.CONTRADICTS]
    assert rate.shared_keys == ["company:банк россии"]
    assert "позитивно" in rate.explanation and "негативно" in rate.explanation
    coal = by_kind[LinkKind.REINFORCES]
    assert coal.confidence == "medium" and coal.topics == [Topic.COMMODITIES]
    assert "в ту же сторону" in coal.explanation
    assert all(link.method == "entities" for t in result.threads for link in t.links)


def test_threads_group_chronologically_and_respect_period() -> None:
    result = threads(ALL, now=NOW, days=7)
    assert len(result.threads) == 3 and result.considered == 6
    steel = next(t for t in result.threads if "Северсталь" in t.title)
    assert [c.news_id for c in steel.news] == ["news:steel-1", "news:steel-2"]
    assert steel.title == "Северсталь: цены на сырьё"
    assert NewsThreads.model_validate_json(result.model_dump_json()) == result
    short = threads(ALL, now=NOW, days=3)
    assert "news:steel-1" not in {c.news_id for t in short.threads for c in t.news}
    assert len(threads(ALL, now=NOW, days=7, limit=1).threads) == 1


def test_weak_or_same_story_overlap_is_not_a_link() -> None:
    no_topic = news(
        "news:steel-3", "Северсталь сменила логотип", 0 * DAY, "neutral",
        entities=["Северсталь"], topics=[Topic.OTHER],
    )  # fmt: skip
    other_company = news(
        "news:rate-3", "Ставка по ипотеке выросла", 0 * DAY, "negative",
        entities=["Сбербанк"], topics=[Topic.KEY_RATE],
    )  # fmt: skip
    duplicate = STEEL_DOWN.model_copy(
        update={
            "id": "news:steel-2-copy",
            "dedup": STEEL_DOWN.dedup.model_copy(update={"cluster_id": "story:steel"}),
        }
    )
    original = STEEL_DOWN.model_copy(
        update={"dedup": STEEL_DOWN.dedup.model_copy(update={"cluster_id": "story:steel"})}
    )
    found = kinds([STEEL_UP, original, duplicate, no_topic, RATE_SOFT, other_company])
    assert ("news:steel-3", "news:steel-1") not in found
    assert ("news:rate-3", "news:rate-1") not in found
    assert len([pair for pair in found if pair[1] == "news:steel-1"]) == 1


def test_links_for_one_news() -> None:
    result = links_for("news:steel-1", ALL)
    assert result is not None
    assert [(link.news_id, link.linked_id) for link in result.links] == [
        ("news:steel-2", "news:steel-1")
    ]
    assert [c.news_id for c in result.cards] == ["news:steel-2"]
    assert links_for("news:missing", ALL) is None
    analytics = STEEL_UP.model_copy(update={"id": "bank:x", "kind": MaterialKind.BANK_ANALYTICS})
    assert links_for("bank:x", [analytics, *ALL]) is None


@pytest.fixture
async def api(monkeypatch: pytest.MonkeyPatch) -> Any:
    app = create_app()

    async def connection() -> Any:
        yield object()

    async def load(connection: Any, kinds: set[MaterialKind], now: Any) -> Any:
        return ALL, ["test-warning"]

    async def no_images(connection: Any) -> set[str]:
        return set()

    monkeypatch.setattr(endpoint, "load", load)
    monkeypatch.setattr(endpoint, "shared_images", no_images)
    monkeypatch.setattr(
        endpoint, "datetime", type("Clock", (), {"now": staticmethod(lambda tz: NOW)})
    )
    app.dependency_overrides[get_connection] = connection
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http


async def test_api_threads_and_links(api: Any) -> None:
    threads_response = await api.get("/api/news/threads?days=7")
    assert threads_response.status_code == 200
    body = threads_response.json()
    assert len(body["threads"]) == 3 and body["warnings"] == ["test-warning"]
    links = await api.get("/api/news/news:rate-2/links")
    assert links.status_code == 200
    assert links.json()["links"][0]["label"] == "Противоречит"
    assert (await api.get("/api/news/news:missing/links")).status_code == 404
    assert (await api.get("/api/news/threads?days=99")).status_code == 422
