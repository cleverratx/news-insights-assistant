from typing import Any

import pytest
from pydantic import ValidationError

from app.contracts.common import BankProduct
from app.contracts.news import Topic
from app.contracts.profile import (
    CompanyProfile,
    Exposure,
    ExposureDirection,
    FeatureKind,
    Location,
    ProfileFeature,
    Scale,
)
from app.profile.okved import industry
from app.profile.products import ProductProfile, product_profile
from app.profile.row import CompanyRow

NO_PRODUCTS = dict.fromkeys(
    (
        "has_loan",
        "has_leasing",
        "has_guarantee",
        "has_factoring",
        "has_deposit",
        "has_settlement",
        "has_payroll",
    ),
    False,
)


def row(**changes: Any) -> CompanyRow:
    base = {"company_id": "U_1", "client_type": "CLIENT", "has_customs_records": False}
    return CompanyRow.from_record({**base, **NO_PRODUCTS, **changes})


def exposure(result: ProductProfile, topic: Topic) -> Exposure:
    return next(item for item in result.exposures if item.topic is topic)


def test_products_become_traceable_features() -> None:
    result = product_profile(row(has_loan=True, has_settlement=True, has_payroll=True))
    assert result.products == [BankProduct.LOAN, BankProduct.SETTLEMENT, BankProduct.PAYROLL]
    features = {feature.id: feature for feature in result.features}
    assert features["feature:loan"].label == "Кредитные продукты в Альфа-Банке"
    assert features["feature:loan"].source_fields == ["CL_PRODUCT_LOA_MARK"]
    assert features["feature:payroll"].source_fields == ["CL_PRODUCT_ZPP_MARK"]
    assert "feature:deposit" not in features
    assert result.missing_fields == []


def test_borrowing_makes_key_rate_a_risk() -> None:
    result = product_profile(row(has_loan=True, has_leasing=True))
    rate = exposure(result, Topic.KEY_RATE)
    assert rate.direction is ExposureDirection.RISK
    assert rate.feature_ids == ["feature:loan", "feature:leasing"]
    assert rate.reason.startswith("Кредиты, лизинг в банке")


def test_deposit_alone_makes_key_rate_an_opportunity() -> None:
    rate = exposure(product_profile(row(has_deposit=True)), Topic.KEY_RATE)
    assert rate.direction is ExposureDirection.OPPORTUNITY
    assert rate.feature_ids == ["feature:deposit"]


def test_debt_and_deposit_make_key_rate_mixed() -> None:
    rate = exposure(product_profile(row(has_guarantee=True, has_deposit=True)), Topic.KEY_RATE)
    assert rate.direction is ExposureDirection.MIXED
    assert rate.feature_ids == ["feature:guarantee", "feature:deposit"]


def test_settlement_only_has_no_rate_exposure() -> None:
    result = product_profile(row(has_settlement=True))
    assert result.exposures == []


def test_payroll_links_labor_market() -> None:
    labor = exposure(product_profile(row(has_payroll=True)), Topic.LABOR)
    assert labor.direction is ExposureDirection.RISK
    assert "дефицит кадров" in labor.keywords


def test_foreign_trade_with_hubs_and_unfriendly_countries() -> None:
    result = product_profile(
        row(has_customs_records=True, customs_countries=["КИТАЙ", "ТУРЦИЯ", "ЛИТВА", "ОАЭ"])
    )
    assert result.foreign_trade.has_customs_records is True
    features = {feature.id: feature for feature in result.features}
    customs = features["feature:customs"]
    assert customs.label == "Ведёт внешнеэкономическую деятельность: Китай, Турция, Литва, ОАЭ"
    assert customs.source_fields == ["FTS_FLAG", "FTS_COUNTRY_NAME"]
    assert customs.kind is FeatureKind.FOREIGN_TRADE
    assert features["feature:unfriendly_trade"].value == "ЛИТВА"
    assert features["feature:payment_hub_trade"].derived is True

    fx = exposure(result, Topic.FX)
    assert fx.direction is ExposureDirection.MIXED
    assert fx.keywords == [
        "курс рубля",
        "курс юаня",
        "курс турецкой лиры",
        "курс евро",
        "курс дирхама",
    ]
    assert "Китай" in exposure(result, Topic.TRADE).keywords
    sanctions = exposure(result, Topic.SANCTIONS)
    assert sanctions.direction is ExposureDirection.RISK
    assert sanctions.feature_ids == [
        "feature:customs",
        "feature:unfriendly_trade",
        "feature:payment_hub_trade",
    ]
    assert "Литва" in sanctions.reason and "Китай" in sanctions.reason


def test_neutral_country_has_fx_but_no_sanctions() -> None:
    result = product_profile(row(has_customs_records=True, customs_countries=["УЗБЕКИСТАН"]))
    topics = {item.topic for item in result.exposures}
    assert topics == {Topic.FX, Topic.TRADE}
    assert "курс сума" in exposure(result, Topic.FX).keywords


def test_customs_flag_without_countries() -> None:
    result = product_profile(row(has_customs_records=True))
    customs = next(feature for feature in result.features if feature.id == "feature:customs")
    assert customs.label == "Ведёт внешнеэкономическую деятельность"
    assert customs.source_fields == ["FTS_FLAG"]
    assert result.missing_fields == ["FTS_COUNTRY_NAME"]


def test_countries_without_flag_are_trusted_and_flagged() -> None:
    result = product_profile(row(has_customs_records=False, customs_countries=["КИТАЙ"]))
    assert result.foreign_trade.has_customs_records is True
    assert "FTS_FLAG = 0" in result.warnings[0]


def test_unknown_flags_are_gaps_not_negatives() -> None:
    result = product_profile(CompanyRow(company_id="U_2"))
    assert result.products == []
    assert result.foreign_trade.has_customs_records is False
    assert "CL_PRODUCT_LOA_MARK" in result.missing_fields
    assert "FTS_FLAG" in result.missing_fields


def test_prospect_without_products_is_explained() -> None:
    result = product_profile(row(client_type="PROSPECT"))
    assert "других банках" in result.warnings[0]


def test_exposures_fit_profile_contract() -> None:
    result = product_profile(
        row(has_loan=True, has_customs_records=True, customs_countries=["КИТАЙ"])
    )
    profile = CompanyProfile(
        company_id="U_1",
        industry=industry("46.38"),
        scale=Scale(),
        location=Location(),
        products=result.products,
        foreign_trade=result.foreign_trade,
        features=result.features,
        exposures=result.exposures,
    )
    assert {item.topic for item in profile.exposures} >= {Topic.KEY_RATE, Topic.SANCTIONS}


def test_profile_rejects_exposure_without_feature() -> None:
    feature = ProfileFeature(
        id="feature:loan", kind=FeatureKind.PRODUCT, label="Кредит", source_fields=["X"]
    )
    with pytest.raises(ValidationError, match="feature:ghost"):
        CompanyProfile(
            company_id="U_1",
            industry=industry(None),
            scale=Scale(),
            location=Location(),
            foreign_trade=product_profile(row()).foreign_trade,
            features=[feature],
            exposures=[
                Exposure(
                    topic=Topic.KEY_RATE,
                    direction=ExposureDirection.RISK,
                    reason="Долг",
                    feature_ids=["feature:ghost"],
                )
            ],
        )
