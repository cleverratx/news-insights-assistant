from typing import Any

import pytest

from app.contracts.common import BankProduct
from app.contracts.news import Classification, NewsItem, Topic
from app.contracts.profile import (
    CompanyGroup,
    CompanyProfile,
    Counterparty,
    Exposure,
    ExposureDirection,
    FeatureKind,
    ForeignTrade,
    Location,
    ProfileFeature,
    RelatedCompany,
    Scale,
    SizeCategory,
)
from app.profile.okved import industry
from app.rank.rules import (
    COUNTERPARTY_BASE,
    COUNTERPARTY_SHARE,
    INDUSTRY_SEGMENT,
    PRODUCT,
    REGION_HOME,
    SCALE_MISMATCH,
    Rule,
    match,
    same_region,
)
from tests.test_news_classify import news

FEATURES = (
    "industry",
    "size",
    "region",
    "city",
    "geography",
    "loan",
    "deposit",
    "payroll",
    "customs",
    "payment_hub_trade",
    "unfriendly_trade",
    "counterparties_outgoing",
    "counterparties_incoming",
    "group",
    "group_segments",
    "person_links",
    "competitors",
    "debt_dependence",
)


def feature(id_: str) -> ProfileFeature:
    return ProfileFeature(
        id=f"feature:{id_}", kind=FeatureKind.INDUSTRY, label=id_, source_fields=["X"]
    )


def profile(**changes: Any) -> CompanyProfile:
    ids = changes.pop("features", FEATURES)
    base: dict[str, Any] = {
        "company_id": "U_1",
        "industry": industry("06.10"),
        "scale": Scale(revenue_rub=2e10, size_category=SizeCategory.LARGEST),
        "location": Location(city="Екатеринбург", region="Свердловская область"),
        "foreign_trade": ForeignTrade(has_customs_records=False),
        "features": [feature(id_) for id_ in ids],
    }
    return CompanyProfile(**{**base, **changes})


def item(title: str = "Новость", summary: str | None = None, **classification: Any) -> NewsItem:
    topics = classification.pop("topics", [Topic.INDUSTRY])
    return news(title).model_copy(
        update={
            "summary": summary,
            "classification": Classification(topics=topics, **classification),
        }
    )


def by_rule(result: Any, rule: Rule) -> list[str]:
    return [hit.feature_id for hit in result.hits if hit.rule is rule]


def test_unclassified_news_matches_nothing() -> None:
    plain = news().model_copy(update={"classification": None})
    result = match(profile(), plain)
    assert result.score == 0
    assert result.hits == []
    assert "не классифицирована" in result.warnings[0]


def test_own_segment_matches_industry() -> None:
    result = match(profile(), item(segments=["oil_gas"]))
    assert by_rule(result, Rule.INDUSTRY) == ["feature:industry"]
    assert result.score == INDUSTRY_SEGMENT
    assert "Нефть, газ" in result.features()[0].reason
    assert result.terms() == ["Нефть, газ и нефтепродукты"]


def test_other_segment_does_not_match() -> None:
    result = match(profile(), item(segments=["retail"], industries=["розничная торговля"]))
    assert result.hits == []
    assert result.score == 0


def test_okved_code_and_industry_words() -> None:
    by_code = match(profile(), item(okved_codes=["06"], segments=["retail"]))
    assert by_rule(by_code, Rule.INDUSTRY) == ["feature:industry"]
    by_words = match(profile(), item(industries=["нефтепродукты и топливо"]))
    assert by_rule(by_words, Rule.INDUSTRY) == ["feature:industry"]
    assert by_words.score < INDUSTRY_SEGMENT


def test_sensitive_topic_counts_only_without_segments() -> None:
    macro = match(profile(), item(topics=[Topic.COMMODITIES]))
    assert by_rule(macro, Rule.INDUSTRY) == ["feature:industry"]
    assert "цены на сырьё" in macro.hits[0].reason
    other = match(profile(), item(topics=[Topic.COMMODITIES], segments=["agro"]))
    assert by_rule(other, Rule.INDUSTRY) == []


def test_unknown_industry_skips_industry_rules() -> None:
    result = match(profile(industry=industry(None)), item(segments=["other"]))
    assert result.hits == []


def test_counterparty_segment_weight_grows_with_share() -> None:
    partners = [
        Counterparty(
            company_id="U_2", direction="outgoing", amount_rub=1, share=0.3, segment="logistics"
        ),
        Counterparty(
            company_id="U_3", direction="outgoing", amount_rub=1, share=0.2, segment="logistics"
        ),
        Counterparty(company_id="U_4", direction="incoming", amount_rub=1, share=0.9),
    ]
    result = match(profile(counterparties=partners), item(segments=["logistics"]))
    [hit] = result.hits
    assert hit.feature_id == "feature:counterparties_outgoing"
    assert hit.weight == pytest.approx(COUNTERPARTY_BASE + COUNTERPARTY_SHARE * 0.5)
    assert "поставщики" in hit.reason and "50%" in hit.reason


def test_group_and_related_companies_segments() -> None:
    group = CompanyGroup(
        group_id="G_1",
        companies_count=3,
        members=[RelatedCompany(company_id="U_5", relation="group", segment="chemicals")],
    )
    related = [RelatedCompany(company_id="U_6", relation="shared_person", segment="chemicals")]
    result = match(profile(group=group, related_companies=related), item(segments=["chemicals"]))
    assert set(by_rule(result, Rule.GROUP)) == {
        "feature:group_segments",
        "feature:group",
        "feature:person_links",
    }


def test_region_names_are_compared_by_stem() -> None:
    assert same_region("Свердловская область", "Свердловской области")
    assert same_region("Республика Татарстан", "Татарстан")
    assert same_region("ПОВОЛЖСКИЙ", "Поволжье")
    assert not same_region("Москва", "Московская область")
    assert not same_region("", "Москва")


def test_home_region_beats_partner_region() -> None:
    home = match(profile(), item(regions=["Свердловской области"]))
    assert by_rule(home, Rule.REGION) == ["feature:region"]
    assert home.score == REGION_HOME
    partners = [
        Counterparty(company_id="U_2", direction="incoming", amount_rub=1, region="Татарстан")
    ]
    remote = match(profile(counterparties=partners), item(regions=["Республика Татарстан"]))
    assert by_rule(remote, Rule.REGION) == ["feature:geography"]
    assert remote.score < REGION_HOME
    nowhere = match(profile(), item(regions=["Приморский край"]))
    assert by_rule(nowhere, Rule.REGION) == ["feature:region"]
    assert 0 < nowhere.score < remote.score
    assert nowhere.penalties


def test_products_need_topic_and_feature() -> None:
    company = profile(products=[BankProduct.LOAN, BankProduct.PAYROLL, BankProduct.SETTLEMENT])
    rate = match(company, item(topics=[Topic.KEY_RATE]))
    assert by_rule(rate, Rule.PRODUCT) == ["feature:loan"]
    assert rate.score == PRODUCT
    labor = match(company, item(topics=[Topic.LABOR]))
    assert by_rule(labor, Rule.PRODUCT) == ["feature:payroll"]
    bare = profile(products=[BankProduct.LOAN], features=["industry"])
    assert match(bare, item(topics=[Topic.KEY_RATE])).hits == []


def test_exposures_match_by_topic_except_specific() -> None:
    exposures = [
        Exposure(
            topic=Topic.KEY_RATE,
            direction=ExposureDirection.RISK,
            reason="Зависимость от заёмных средств высокая",
            feature_ids=["feature:debt_dependence", "feature:loan"],
        ),
        Exposure(
            topic=Topic.INDUSTRY,
            direction=ExposureDirection.MIXED,
            reason="Компании группы в химии",
            feature_ids=["feature:group_segments"],
        ),
    ]
    company = profile(products=[BankProduct.LOAN], exposures=exposures)
    rate = match(company, item(topics=[Topic.KEY_RATE]))
    assert by_rule(rate, Rule.EXPOSURE) == ["feature:debt_dependence", "feature:loan"]
    assert [found.feature_id for found in rate.features()] == [
        "feature:loan",
        "feature:debt_dependence",
    ]
    assert rate.score == pytest.approx(1 - (1 - PRODUCT) * (1 - 0.3))
    assert match(company, item(topics=[Topic.INDUSTRY])).hits == []


def test_competitor_named_in_news() -> None:
    exposures = [
        Exposure(
            topic=Topic.COMPANY,
            direction=ExposureDirection.MIXED,
            reason="Конкуренты компании",
            feature_ids=["feature:competitors"],
            keywords=["Акрон"],
        )
    ]
    company = profile(exposures=exposures)
    by_entity = match(company, item(entities=["ПАО «Акрон»"]))
    assert by_rule(by_entity, Rule.ENTITY) == ["feature:competitors"]
    by_title = match(company, item("Акрон запустил новый завод"))
    assert by_rule(by_title, Rule.ENTITY) == ["feature:competitors"]
    assert match(company, item("Макрономика недели")).hits == []


def test_foreign_trade_countries() -> None:
    trade = ForeignTrade(has_customs_records=True, countries=["КИТАЙ", "ЛИТВА"])
    company = profile(foreign_trade=trade)
    china = match(company, item(countries=["КНР"]))
    assert by_rule(china, Rule.FOREIGN_TRADE) == ["feature:customs", "feature:payment_hub_trade"]
    assert "Китай" in china.hits[0].reason
    lithuania = match(company, item(countries=["Литва"]))
    assert by_rule(lithuania, Rule.FOREIGN_TRADE) == ["feature:customs", "feature:unfriendly_trade"]
    assert match(company, item(countries=["Индия"])).hits == []


def test_sme_news_is_penalized_for_large_business() -> None:
    title = "Льготные кредиты для МСП продлят"
    large = match(profile(), item(title, topics=[Topic.REGULATION], segments=["oil_gas"]))
    assert large.score == pytest.approx(INDUSTRY_SEGMENT * SCALE_MISMATCH)
    assert "малого и среднего" in large.penalties[0]
    small = profile(scale=Scale(revenue_rub=5e8, size_category=SizeCategory.SMALL))
    fit = match(small, item(title, segments=["oil_gas"]))
    assert by_rule(fit, Rule.SCALE) == ["feature:size"]
    assert fit.penalty == 1


def test_large_business_news_matches_size() -> None:
    result = match(
        profile(), item("Налог для крупных компаний", summary="Новые правила", segments=[])
    )
    assert by_rule(result, Rule.SCALE) == ["feature:size"]


def test_rules_classification_adds_warning() -> None:
    result = match(profile(), item(segments=["oil_gas"], method="rules"))
    assert "по словарю" in result.warnings[0]
    assert result.rules == [Rule.INDUSTRY]
