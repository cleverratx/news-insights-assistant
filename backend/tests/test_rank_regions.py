from datetime import timedelta
from typing import Any

from app.api.feed import build_feed
from app.contracts.news import Topic
from app.contracts.profile import (
    CompanyGroup,
    Counterparty,
    FeatureKind,
    ForeignTrade,
    ProfileFeature,
    RelatedCompany,
)
from app.contracts.rank import GeoScope
from app.rank.llm_filter import verify
from app.rank.regions import locate, market_places, presence
from app.rank.rules import REGION_MARKET, Rule, match
from app.rank.score import score
from tests.test_api_feed import answer, material
from tests.test_news_classify import NOW, FakeLLM
from tests.test_rank_rules import FEATURES, item, profile

MARKETS = "feature:knowledge:markets.sales_regions"


def market(text: str) -> ProfileFeature:
    return ProfileFeature(
        id=MARKETS,
        kind=FeatureKind.KNOWLEDGE,
        label=f"Со слов клиента: {text}",
        value=text,
        source_fields=["knowledge:f1"],
    )


def with_market(company: Any, text: str) -> Any:
    return company.model_copy(update={"features": [*company.features, market(text)]})


def rich(**changes: Any) -> Any:
    company = profile(
        features=(*FEATURES, "persons"),
        counterparties=[
            Counterparty(company_id="U_2", direction="incoming", amount_rub=1, region="Москва")
        ],
        group=CompanyGroup(
            group_id="G",
            companies_count=2,
            members=[RelatedCompany(company_id="U_3", relation="group", region="Омская область")],
        ),
        person_regions=["ПОВОЛЖСКИЙ"],
        foreign_trade=ForeignTrade(has_customs_records=True, countries=["КИТАЙ"]),
        **changes,
    )
    return with_market(company, "Татарстан и Казахстан")


def test_presence_collects_every_source() -> None:
    sources = {(place.name, place.source, place.feature_id) for place in presence(rich())}
    assert ("Свердловская область", "home", "feature:region") in sources
    assert ("Москва", "counterparty", "feature:counterparties_incoming") in sources
    assert ("Омская область", "group", "feature:group") in sources
    assert ("ПОВОЛЖСКИЙ", "person", "feature:persons") in sources
    assert ("Татарстан", "market", MARKETS) in sources
    assert ("Казахстан", "market", MARKETS) in sources
    assert ("КИТАЙ", "trade", "feature:customs") in sources


def test_market_places_split_client_text() -> None:
    company = with_market(profile(features=[]), "Москва; Пермский край, Казахстан")
    names = [place.name for place in market_places(company)]
    assert names == ["Москва", "Пермский край", "Казахстан"]


def test_scopes() -> None:
    company = rich()
    home = locate(item(regions=["Свердловской области"], topics=[Topic.REGIONAL]), company)
    assert home.scope is GeoScope.HOME
    partner = locate(item(regions=["Москва"], topics=[Topic.REGIONAL]), company)
    assert partner.scope is GeoScope.PRESENCE and partner.matched[0].source == "counterparty"
    client = locate(item(regions=["Республика Татарстан"], topics=[Topic.REGIONAL]), company)
    assert client.scope is GeoScope.PRESENCE and "рынки со слов клиента" in client.detail
    trade = locate(item(countries=["КНР"]), company)
    assert trade.scope is GeoScope.PRESENCE
    assert locate(item(), company).scope is GeoScope.NATIONAL
    assert (
        locate(item(regions=["Россия"], topics=[Topic.REGIONAL]), company).scope
        is GeoScope.NATIONAL
    )


def test_only_regional_events_elsewhere_are_demoted() -> None:
    company = rich()
    away = locate(item(regions=["Приморский край"], topics=[Topic.REGIONAL]), company)
    assert away.scope is GeoScope.OTHER_REGION
    assert "опущена в конец ленты" in away.detail
    mentioned = locate(item(regions=["Рязанская область"], topics=[Topic.COMPANY]), company)
    assert mentioned.scope is GeoScope.NATIONAL
    abroad = locate(item(countries=["Таиланд"], topics=[Topic.REGIONAL]), company)
    assert abroad.scope is GeoScope.NATIONAL


def test_client_market_region_counts_in_rules() -> None:
    result = match(rich(), item(regions=["Татарстан"]))
    [hit] = [hit for hit in result.hits if hit.rule is Rule.REGION]
    assert (hit.feature_id, hit.weight) == (MARKETS, REGION_MARKET)
    assert "со слов клиента" in hit.reason


async def test_feed_demotes_other_region_without_hiding_it() -> None:
    company = rich()
    far = material(
        "far",
        "Нефть Приморья",
        segments=["oil_gas"],
        regions=["Приморский край"],
        topics=[Topic.REGIONAL, Topic.INDUSTRY],
    )
    near = material("near", "Нефть", age=timedelta(days=3), segments=["oil_gas"])
    feed = await build_feed(company, [far, near], None, now=NOW)
    assert [card.news_id for card in feed.items] == ["near", "far"]
    assert feed.items[0].importance.score < feed.items[1].importance.score
    assert feed.items[1].geography.scope is GeoScope.OTHER_REGION
    assert feed.demoted_foreign == 1
    assert any("опущены в конец ленты" in note for note in feed.adjustments)


async def test_model_sees_local_candidates_first() -> None:
    company = rich()
    far = material("far", "Нефть Приморья", segments=["oil_gas"], regions=["Приморский край"])
    near = material("near", "Нефть", age=timedelta(days=3), segments=["oil_gas"])
    pairs = [(news, score(company, news, NOW)) for news in (far, near)]
    llm = FakeLLM(answer(("n1", True, 0.9)))
    result = await verify(llm, company, pairs, limit=1, demoted={"far"})
    assert "Нефть Приморья" not in llm.calls[0][1].content
    assert result.items[0].news_id == "near"
