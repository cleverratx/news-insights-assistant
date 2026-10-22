import json
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.api import feed as feed_api
from app.api.companies import get_connection
from app.api.feed import build_feed, get_llm
from app.contracts.feed import Feed
from app.contracts.news import Channel, Dedup, Marker, MaterialKind, NewsItem, Source, Topic
from app.contracts.rank import RankStage
from app.main import create_app
from tests.test_news_classify import NOW, FakeLLM
from tests.test_rank_rules import item, profile


def material(
    id_: str,
    title: str,
    source: str = "Ведомости",
    cluster: str | None = None,
    age: timedelta = timedelta(0),
    **classification: Any,
) -> NewsItem:
    return item(title, **classification).model_copy(
        update={
            "id": id_,
            "source": Source(name=source, channel=Channel.RSS),
            "published_at": NOW - age,
            "dedup": Dedup(content_hash=id_, cluster_id=cluster),
        }
    )


def answer(*verdicts: tuple[str, bool, float]) -> str:
    return json.dumps(
        {
            "verdicts": [
                {"id": key, "relevant": ok, "score": value, "reason": f"Аргумент {key}"}
                for key, ok, value in verdicts
            ]
        },
        ensure_ascii=False,
    )


OIL = material("news:oil-1", "Нефть дорожает", cluster="story:oil", segments=["oil_gas"])
OIL_REPOST = material(
    "news:oil-2", "Нефть дорожает снова", "РБК", "story:oil", timedelta(hours=2), segments=["agro"]
)
RETAIL = material("news:retail", "Розница растёт", segments=["retail"])
REGION = material("news:region", "Свердловская область", regions=["Свердловская область"])


async def test_story_collapses_and_model_confirms() -> None:
    llm = FakeLLM(answer(("n1", True, 0.9)))
    result = await build_feed(profile(), [OIL, OIL_REPOST, RETAIL], llm, now=NOW)
    [shown] = result.items
    assert shown.news_id == "news:oil-1"
    assert (shown.story_id, shown.coverage) == ("story:oil", 2)
    assert shown.stage is RankStage.RERANKED
    assert shown.explanation == "Аргумент n1"
    assert shown.event.type == "Отраслевое событие"
    assert shown.event.markers == [Marker.INDUSTRY_EVENT]
    assert shown.card.source == "Ведомости"
    assert shown.features[0].feature_id == "feature:industry"
    assert shown.features[0].label == "industry"
    assert shown.score.llm == 0.9
    assert "пишут 2 изданий" in shown.score.factors[1]
    assert (result.verified, result.model) == (True, "deepseek-v4.1-flash")
    assert (result.considered, result.dropped_by_rules) == (2, 1)


async def test_rejected_by_model_are_listed_with_argument() -> None:
    llm = FakeLLM(answer(("n1", False, 0.1)))
    result = await build_feed(profile(), [OIL], llm, now=NOW)
    assert result.items == []
    [rejected] = result.rejected
    assert (rejected.news_id, rejected.title, rejected.reason) == (
        "news:oil-1",
        "Нефть дорожает",
        "Аргумент n1",
    )


async def test_without_model_feed_uses_rules() -> None:
    result = await build_feed(profile(), [OIL, REGION, RETAIL], None, now=NOW)
    assert [found.news_id for found in result.items] == ["news:oil-1", "news:region"]
    assert all(found.stage is RankStage.CANDIDATE for found in result.items)
    assert [found.rank for found in result.items] == [1, 2]
    assert not result.verified
    assert "порядок по правилам" in result.warnings[0]
    assert result.items[1].explanation.startswith("Новость о регионе")


async def test_unverified_are_hidden_unless_asked() -> None:
    llm = FakeLLM(answer(("n1", True, 0.9)))
    hidden = await build_feed(profile(), [OIL, REGION], llm, now=NOW)
    assert [found.news_id for found in hidden.items] == ["news:oil-1"]
    shown = await build_feed(profile(), [OIL, REGION], llm, now=NOW, unverified=True)
    assert [found.stage for found in shown.items] == [RankStage.RERANKED, RankStage.CANDIDATE]
    limited = await build_feed(profile(), [OIL, REGION], llm, now=NOW, unverified=True, limit=1)
    assert len(limited.items) == 1


async def test_period_and_markers_filter() -> None:
    old = material("news:old", "Старая нефть", age=timedelta(days=10), segments=["oil_gas"])
    bank = material("bank:1", "Обзор нефти", age=timedelta(days=60), segments=["oil_gas"])
    bank = bank.model_copy(update={"kind": MaterialKind.BANK_ANALYTICS})
    result = await build_feed(profile(), [old, bank, REGION], None, now=NOW, days=7)
    assert {found.news_id for found in result.items} == {"bank:1", "news:region"}
    assert (
        next(found for found in result.items if found.news_id == "bank:1").kind == "bank_analytics"
    )
    rate = material("news:rate", "Ставка", topics=[Topic.KEY_RATE], segments=["oil_gas"])
    only = await build_feed(profile(), [OIL, rate], None, now=NOW, markers=[Marker.ECONOMY_RATE])
    assert [found.news_id for found in only.items] == ["news:rate"]


@pytest.fixture
async def api(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    async def company(company_id: str, connection: Any) -> Any:
        return profile(company_id=company_id)

    async def materials(connection: Any, kinds: Any, now: Any) -> Any:
        return [OIL.model_copy(update={"published_at": now})], ["Аналитика банка не загружена"]

    async def images(connection: Any) -> set[str]:
        return set()

    monkeypatch.setattr(feed_api, "company_profile", company)
    monkeypatch.setattr(feed_api, "load", materials)
    monkeypatch.setattr(feed_api, "shared_images", images)
    monkeypatch.setattr(feed_api, "LLMCache", lambda connection: None)
    app = create_app()

    async def connection() -> AsyncIterator[Any]:
        yield object()

    async def no_llm() -> AsyncIterator[Any]:
        yield None

    app.dependency_overrides[get_connection] = connection
    app.dependency_overrides[get_llm] = no_llm
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def test_feed_endpoint(api: AsyncClient) -> None:
    response = await api.get(
        "/api/companies/U_7/feed", params={"days": 3, "markers": "industry_event"}
    )
    assert response.status_code == 200
    body = Feed.model_validate(response.json())
    assert body.company_id == "U_7"
    assert body.period_days == 3
    assert [found.news_id for found in body.items] == ["news:oil-1"]
    assert body.warnings[0] == "Аналитика банка не загружена"
    assert (await api.get("/api/companies/U_7/feed", params={"days": 100})).status_code == 422
    assert (await api.get("/api/companies/U_7/feed", params={"markers": "x"})).status_code == 422


async def test_feed_accepts_role_and_rejects_unknown(api: AsyncClient) -> None:
    response = await api.get("/api/companies/U_7/feed?role=cfo")
    assert response.status_code == 200
    assert response.json()["role"] == "cfo"
    assert (await api.get("/api/companies/U_7/feed?role=unknown")).status_code == 422
    assert (await api.get("/api/companies/U_7/feed")).json()["role"] is None
