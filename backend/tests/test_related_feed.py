from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.api import related_feed as endpoint
from app.api.companies import get_connection
from app.contracts.competitors import Competitor, Competitors, CompetitorSource
from app.contracts.links import LinkedCompanies, LinkedCompanyCard, LinkOrigin, PaymentTotals
from app.contracts.news import MaterialKind
from app.contracts.related_feed import RelatedFeed, RelatedScope
from app.main import create_app
from app.rank.related import build_related_feed
from tests.test_api_feed import material
from tests.test_news_classify import NOW


def rivals() -> Competitors:
    return Competitors(
        company_id="U_1",
        competitors=[
            Competitor(
                id="client:rival",
                source=CompetitorSource.CLIENT,
                name="ПАО «Северсталь»",
                reasons=["Назвал клиент"],
            )
        ],
        excluded=[
            Competitor(
                id="client:excluded",
                source=CompetitorSource.CLIENT,
                name="Акрон",
                reasons=["Исключён клиентом"],
            )
        ],
    )


def linked() -> LinkedCompanies:
    totals = PaymentTotals(amount_rub=0, payments_count=0, counterparties=0)
    return LinkedCompanies(
        company_id="U_1",
        incoming=totals,
        outgoing=totals,
        cards=[
            LinkedCompanyCard(
                id="manual:carrier",
                name="РЖД",
                origins=[LinkOrigin.CLIENT],
            )
        ],
    )


def test_entity_alias_and_text_matches_with_evidence() -> None:
    entity = material("news:1", "Перевозчик изменил тариф", entities=["ОАО РЖД"])
    text = material("news:2", "Северсталь запустила завод")
    result = build_related_feed("U_1", [entity, text], rivals(), linked(), now=NOW)
    assert result.matched == 2
    matches = {m.id: m for event in result.items for m in event.companies}
    assert matches["manual:carrier"].method == "entity"
    assert matches["manual:carrier"].origins == [LinkOrigin.CLIENT]
    assert matches["client:rival"].method == "text"
    assert "Северсталь" in matches["client:rival"].evidence
    assert matches["client:rival"].news_id == text.id
    assert result.warnings
    assert RelatedFeed.model_validate_json(result.model_dump_json()) == result


def test_unrelated_same_industry_substring_and_excluded_are_not_selected() -> None:
    news = [
        material("news:industry", "Сталь дорожает", segments=["metals"]),
        material("news:substring", "Северстальстрой расширяется"),
        material("news:excluded", "Акрон сообщил о сделке", entities=["Акрон"]),
    ]
    assert build_related_feed("U_1", news, rivals(), linked(), now=NOW).items == []


@pytest.mark.parametrize(
    "scope,expected",
    [
        (RelatedScope.ALL, 2),
        (RelatedScope.COMPETITORS, 1),
        (RelatedScope.COUNTERPARTIES, 1),
    ],
)
def test_scopes(scope: RelatedScope, expected: int) -> None:
    news = material("news:1", "Северсталь и РЖД подписали договор", entities=["РЖД"])
    result = build_related_feed("U_1", [news], rivals(), linked(), now=NOW, scope=scope)
    assert len(result.items[0].companies) == expected


def test_story_aggregates_companies_preserves_evidence_and_has_no_duplicates() -> None:
    first = material("news:1", "Северсталь", cluster="story:1", age=timedelta(hours=1))
    second = material("news:2", "Договор с РЖД", cluster="story:1", entities=["РЖД"])
    result = build_related_feed("U_1", [first, second, first], rivals(), linked(), now=NOW)
    [event] = result.items
    assert result.considered == 2
    assert event.story_id == "story:1"
    assert len(event.companies) == len(event.publications) == 2
    assert {m.news_id for m in event.companies} == {first.id, second.id}


def test_date_window_limit_and_bank_analytics_excluded() -> None:
    old = material("news:old", "Северсталь", age=timedelta(days=8))
    future = material("news:future", "Северсталь", age=timedelta(days=-1))
    bank = material("bank:1", "Северсталь").model_copy(update={"kind": MaterialKind.BANK_ANALYTICS})
    early = material("news:early", "Северсталь", age=timedelta(hours=1))
    latest = material("news:latest", "Северсталь")
    result = build_related_feed(
        "U_1",
        [old, future, bank, early, latest],
        rivals(),
        linked(),
        now=NOW,
        limit=1,
    )
    assert result.considered == result.matched == 2
    assert [e.news_id for e in result.items] == [latest.id]


def test_unnamed_companies_are_reported_and_no_targets_gives_empty_feed() -> None:
    unnamed = Competitor(
        id="U_2",
        source=CompetitorSource.BANK_DATA,
        company_id="U_2",
        reasons=["Тот же рынок"],
    )
    competitors = rivals().model_copy(update={"competitors": [unnamed]})
    links = linked().model_copy(update={"cards": []})
    result = build_related_feed(
        "U_1",
        [material("news:1", "U_2")],
        competitors,
        links,
        now=NOW,
    )
    assert result.items == [] and result.unnamed_companies == 1
    assert result.warnings


def test_lists_must_belong_to_customer() -> None:
    with pytest.raises(ValueError):
        build_related_feed("U_2", [], rivals(), linked(), now=NOW)


async def test_endpoint_parameters_and_fresh_lists(monkeypatch: pytest.MonkeyPatch) -> None:
    async def competitors(connection: Any, company_id: str) -> Competitors:
        return rivals()

    async def links(connection: Any, company_id: str) -> LinkedCompanies:
        return linked()

    async def load(connection: Any, kinds: Any, now: Any) -> Any:
        return [material("news:1", "Северсталь").model_copy(update={"published_at": now})], []

    async def images(connection: Any) -> set[str]:
        return set()

    async def connection() -> AsyncIterator[Any]:
        yield object()

    monkeypatch.setattr(endpoint, "company_competitors", competitors)
    monkeypatch.setattr(endpoint, "company_links", links)
    monkeypatch.setattr(endpoint, "load", load)
    monkeypatch.setattr(endpoint, "shared_images", images)
    app = create_app()
    app.dependency_overrides[get_connection] = connection
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/companies/U_1/related-feed?scope=competitors")
        assert response.status_code == 200
        assert (
            RelatedFeed.model_validate(response.json()).items[0].companies[0].id == "client:rival"
        )
        for query in ("scope=unknown", "days=15", "limit=0"):
            assert (await client.get(f"/api/companies/U_1/related-feed?{query}")).status_code == 422
        monkeypatch.setattr(endpoint, "company_competitors", empty_competitors)
        assert (await client.get("/api/companies/U_1/related-feed")).json()["items"] == []


async def empty_competitors(connection: Any, company_id: str) -> Competitors:
    return Competitors(company_id=company_id)


def test_text_alias_and_original_quote_are_preserved() -> None:
    news = material("news:alias", "ООО «РЖД» повысило тарифы")
    result = build_related_feed("U_1", [news], rivals(), linked(), now=NOW)
    [mention] = result.items[0].companies
    assert mention.method == "text"
    assert news.title in mention.evidence


def test_deleted_link_is_not_selected() -> None:
    news = material("news:carrier", "РЖД повысило тарифы", entities=["РЖД"])
    links = linked().model_copy(update={"cards": []})
    assert build_related_feed("U_1", [news], rivals(), links, now=NOW).items == []


def test_fact_actor_is_enough_even_without_classification_entity() -> None:
    from app.contracts.news import FactStatus, KeyFact

    news = material("news:fact", "Новый договор")
    # Проверенная фактура A3-05 также участвует в индексе сущностей A3-11.
    fact = KeyFact(
        id="fact:1",
        text="РЖД заключило договор",
        who=["РЖД"],
        status=FactStatus.CONFIRMED,
    )
    news = news.model_copy(update={"key_facts": [fact]})
    result = build_related_feed("U_1", [news], rivals(), linked(), now=NOW)
    assert result.items[0].companies[0].method == "entity"


def test_body_only_match_preserves_literal_evidence() -> None:
    body = "Начало. Компания ПАО «Северсталь» объявила о запуске производства. Конец."
    news = material("news:body", "Запуск производства").model_copy(update={"text": body})
    result = build_related_feed("U_1", [news], rivals(), linked(), now=NOW)
    [mention] = result.items[0].companies
    assert mention.method == "text"
    assert "«Северсталь»" in mention.evidence
    assert mention.evidence in f"{news.title}\n{body}"


def test_same_company_in_both_lists_keeps_both_relationships() -> None:
    links = linked().model_copy(
        update={
            "cards": [
                LinkedCompanyCard(
                    id="manual:rival",
                    name="Северсталь",
                    origins=[LinkOrigin.CLIENT],
                )
            ]
        }
    )
    news = material("news:both", "Северсталь объявила о сделке")
    result = build_related_feed("U_1", [news], rivals(), links, now=NOW)
    assert {m.relationship for m in result.items[0].companies} == {"competitor", "counterparty"}
