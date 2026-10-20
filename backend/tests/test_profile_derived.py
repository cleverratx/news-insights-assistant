from typing import Any

from app.contracts.news import Topic
from app.contracts.profile import (
    CompanyGroup,
    CompanyProfile,
    Counterparty,
    ExposureDirection,
    RelatedCompany,
)
from app.profile.derived import DerivedProfile, derive, merge_exposures
from app.profile.features import base_features
from app.profile.graph import LinkProfile
from app.profile.okved import industry
from app.profile.products import product_profile
from app.profile.row import CompanyRow

FLAGS = dict.fromkeys(
    (
        "has_loan",
        "has_leasing",
        "has_guarantee",
        "has_factoring",
        "has_deposit",
        "has_settlement",
        "has_payroll",
        "has_customs_records",
    ),
    False,
)


def row(**changes: Any) -> CompanyRow:
    base = {
        "company_id": "U_1",
        "okved_code": "46.38",
        "region": "Москва",
        "client_type": "CLIENT",
    }
    return CompanyRow.from_record({**base, **FLAGS, **changes})


def partner(
    direction: str, share: float, segment: str | None = None, region: str | None = None
) -> Counterparty:
    return Counterparty(
        company_id=f"U_{share}",
        direction=direction,  # type: ignore[arg-type]
        amount_rub=share * 1000,
        share=share,
        segment=segment,
        region=region,
    )


def run(company: CompanyRow, links: LinkProfile | None = None) -> DerivedProfile:
    return derive(company, base_features(company), product_profile(company), links or LinkProfile())


def features(result: DerivedProfile) -> dict[str, Any]:
    return {feature.id: feature for feature in result.features}


def exposure(result: DerivedProfile, topic: Topic) -> Any:
    return next(item for item in result.exposures if item.topic is topic)


def test_two_borrowing_products_mean_high_debt_dependence() -> None:
    result = run(row(has_loan=True, has_leasing=True))
    debt = features(result)["feature:debt_dependence"]
    assert debt.value == "high"
    assert debt.derived is True
    assert debt.source_fields == ["CL_PRODUCT_LOA_MARK", "CL_PRODUCT_LEA_MARK"]
    rate = exposure(result, Topic.KEY_RATE)
    assert rate.direction is ExposureDirection.RISK
    assert rate.feature_ids == ["feature:debt_dependence", "feature:loan", "feature:leasing"]


def test_payments_to_finance_reveal_debt_in_other_banks() -> None:
    links = LinkProfile(counterparties=[partner("outgoing", 0.15, "finance")])
    result = run(row(), links)
    found = features(result)
    assert found["feature:finance_payments"].label == (
        "Платежи финансовым и лизинговым компаниям: 15% исходящих"
    )
    assert found["feature:debt_dependence"].value == "high"
    assert "в других банках" in exposure(result, Topic.KEY_RATE).reason


def test_single_product_is_medium_and_deposit_makes_it_mixed() -> None:
    result = run(row(has_guarantee=True, has_deposit=True))
    assert features(result)["feature:debt_dependence"].value == "medium"
    rate = exposure(result, Topic.KEY_RATE)
    assert rate.direction is ExposureDirection.MIXED
    assert rate.feature_ids[-1] == "feature:deposit"


def test_no_debt_evidence_gives_no_debt_feature() -> None:
    result = run(row(has_settlement=True))
    assert "feature:debt_dependence" not in features(result)


def test_wholesaler_with_customs_is_likely_importer() -> None:
    result = run(row(has_customs_records=True, customs_countries=["КИТАЙ"]))
    direction = features(result)["feature:trade_direction"]
    assert direction.value == "import"
    assert direction.label.startswith("Вероятно импортёр (оценка:")
    fx = exposure(result, Topic.FX)
    assert fx.direction is ExposureDirection.RISK
    assert fx.feature_ids == ["feature:customs", "feature:trade_direction"]
    assert "курс юаня" in fx.keywords


def test_miner_with_customs_is_likely_exporter() -> None:
    result = run(row(okved_code="07.10", has_customs_records=True))
    assert features(result)["feature:trade_direction"].value == "export"
    assert exposure(result, Topic.FX).direction is ExposureDirection.OPPORTUNITY


def test_services_with_customs_direction_unknown() -> None:
    result = run(row(okved_code="62.01", has_customs_records=True))
    assert "feature:trade_direction" not in features(result)
    assert "Направление ВЭД" in result.warnings[0]


def test_customer_concentration_levels() -> None:
    links = LinkProfile(
        counterparties=[
            partner("incoming", 0.6),
            partner("incoming", 0.3),
            partner("outgoing", 0.2),
            partner("outgoing", 0.1),
        ]
    )
    result = run(row(), links)
    found = features(result)
    customer = found["feature:customer_concentration"]
    assert customer.value == "high"
    assert customer.label == "Крупнейший покупатель — 60% выручки, три крупнейших — 90%"
    assert found["feature:supplier_concentration"].value == "low"
    risks = [item for item in result.exposures if item.topic is Topic.COMPANY]
    assert len(risks) == 1
    assert risks[0].feature_ids == ["feature:customer_concentration"]


def test_geography_from_partners_group_and_persons() -> None:
    links = LinkProfile(
        counterparties=[partner("incoming", 0.5, region="Татарстан")],
        group=CompanyGroup(
            group_id="g",
            companies_count=2,
            members=[
                RelatedCompany(company_id="U_2", relation="group", region="Новосибирская область")
            ],
        ),
        person_regions=["СИБИРСКИЙ"],
    )
    result = run(row(), links)
    geography = features(result)["feature:geography"]
    assert geography.label == (
        "География присутствия: регистрация — Москва; контрагенты и компании группы — "
        "Татарстан, Новосибирская область; связанные лица — СИБИРСКИЙ"
    )
    regional = exposure(result, Topic.REGIONAL)
    assert regional.keywords == ["Москва", "Татарстан", "Новосибирская область"]


def test_geography_needs_more_than_home_region() -> None:
    assert "feature:geography" not in features(run(row()))


def test_merge_keeps_refined_topic_and_all_industries() -> None:
    company = row(has_loan=True, has_customs_records=True, customs_countries=["КИТАЙ"])
    products = product_profile(company)
    derived = derive(company, base_features(company), products, LinkProfile())
    merged = merge_exposures(products.exposures, derived.exposures)
    topics = [item.topic for item in merged]
    assert len(topics) == len(set(topics))
    assert (
        next(item for item in merged if item.topic is Topic.FX).direction is ExposureDirection.RISK
    )
    assert (
        "feature:debt_dependence"
        in next(item for item in merged if item.topic is Topic.KEY_RATE).feature_ids
    )


def test_full_profile_assembles_into_contract() -> None:
    company = row(has_loan=True, has_customs_records=True, customs_countries=["ТУРЦИЯ"])
    base = base_features(company)
    products = product_profile(company)
    links = LinkProfile(counterparties=[partner("incoming", 0.7, "food", "Татарстан")])
    derived = derive(company, base, products, links)
    profile = CompanyProfile(
        company_id=company.company_id,
        client_type=base.client_type,
        industry=industry(company.okved_code),
        scale=base.scale,
        location=base.location,
        products=products.products,
        foreign_trade=products.foreign_trade,
        counterparties=links.counterparties,
        features=[*base.features, *products.features, *links.features, *derived.features],
        exposures=merge_exposures(products.exposures, links.exposures, derived.exposures),
    )
    assert {item.topic for item in profile.exposures} >= {
        Topic.KEY_RATE,
        Topic.FX,
        Topic.COMPANY,
        Topic.REGIONAL,
    }
