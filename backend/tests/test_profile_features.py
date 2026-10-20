from decimal import Decimal
from typing import Any

import pytest

from app.contracts.profile import ClientType, CompanyProfile, ForeignTrade, SizeCategory
from app.profile.features import base_features, money, size_category
from app.profile.okved import industry
from app.profile.row import CompanyRow

FULL = {
    "company_id": "U_1",
    "ownership": "СОВМЕСТНАЯ ЧАСТНАЯ И ИНОСТРАННАЯ СОБСТВЕННОСТЬ",
    "age_band": "8-15",
    "lifecycle_stage": "Оптимизация",
    "employees_fns": "16-100",
    "client_type": "CLIENT",
    "revenue_rub": Decimal("18200000000.00"),
    "city": "г. Москва",
    "region": "Москва",
    "customs_countries": ["КИТАЙ"],
}


def row(**changes: Any) -> CompanyRow:
    return CompanyRow.from_record({**FULL, **changes})


def by_id(result: Any) -> dict[str, Any]:
    return {feature.id: feature for feature in result.features}


def test_full_row_gives_traceable_features() -> None:
    result = base_features(row())
    features = by_id(result)

    assert features["feature:revenue"].label == "Выручка 18,2 млрд ₽ в год"
    assert features["feature:revenue"].source_fields == ["REVENUE_COMPANY"]
    assert features["feature:size"].value == "largest"
    assert features["feature:size"].derived is True
    assert features["feature:employees"].label == "Сотрудников: 16–100 (по данным ФНС)"
    assert features["feature:employees"].source_fields == ["AMOUNT_WORKERS_FNS_GRADATION"]
    assert features["feature:age"].label == "Возраст компании: 8–15 лет"
    assert features["feature:stage"].source_fields == ["ECO_MARK_LABEL"]
    assert features["feature:foreign_capital"].source_fields == ["OKFS_NAME"]
    assert "feature:state_capital" not in features
    assert features["feature:city"].value == "Москва"
    assert features["feature:client_type"].label == "Действующий клиент банка"

    assert result.scale.is_large_business is True
    assert result.scale.size_category is SizeCategory.LARGEST
    assert result.location.city == "Москва"
    assert result.client_type is ClientType.CLIENT
    assert result.missing_fields == []
    assert result.warnings == []


@pytest.mark.parametrize(
    ("revenue", "expected"),
    [
        ("120000000", SizeCategory.MICRO),
        ("120000001", SizeCategory.SMALL),
        ("800000000", SizeCategory.SMALL),
        ("2000000000", SizeCategory.MEDIUM),
        ("2000000001", SizeCategory.LARGE),
        ("14999999999", SizeCategory.LARGE),
        ("15000000000", SizeCategory.LARGEST),
    ],
)
def test_size_thresholds(revenue: str, expected: SizeCategory) -> None:
    assert size_category(Decimal(revenue)) is expected


@pytest.mark.parametrize(
    ("value", "text"),
    [("18200000000", "18,2 млрд ₽"), ("1000000000", "1 млрд ₽"), ("33600000", "33,6 млн ₽"),
     ("136493", "136,5 тыс ₽"), ("500", "500 ₽")],
)  # fmt: skip
def test_money_is_readable(value: str, text: str) -> None:
    assert money(Decimal(value)) == text


def test_empty_row_does_not_fail_and_lists_gaps() -> None:
    result = base_features(CompanyRow(company_id="U_2"))
    assert result.features == []
    assert result.scale.is_large_business is None
    assert result.scale.size_category is None
    assert result.client_type is None
    assert set(result.missing_fields) == {
        "REVENUE_COMPANY",
        "AMOUNT_WORKERS_FNS_GRADATION",
        "COMPANY_AGE_YEARS_GRADATION",
        "ECO_MARK_LABEL",
        "OKFS_NAME",
        "REGION",
        "CITY",
        "CL_TYPE",
    }


def test_zero_revenue_is_unknown_size_not_micro() -> None:
    result = base_features(row(revenue_rub=Decimal(0)))
    assert "feature:size" not in by_id(result)
    assert result.scale.is_large_business is None
    assert "Выручка равна нулю" in result.warnings[0]


def test_spark_used_when_fns_missing() -> None:
    features = by_id(base_features(row(employees_fns=None, employees_spark="101-250")))
    assert features["feature:employees"].label.endswith("(по данным СПАРК)")
    assert features["feature:employees"].source_fields == ["AMOUNT_WORKERS_SPARK_GRADATION"]


def test_fns_and_spark_disagreement_is_flagged() -> None:
    result = base_features(row(employees_spark="101-250"))
    assert by_id(result)["feature:employees"].value == "16-100"
    assert "расходится" in result.warnings[0]


def test_huge_revenue_with_tiny_staff_is_flagged() -> None:
    result = base_features(row(employees_fns="Менее 15"))
    assert any("холдинг" in warning for warning in result.warnings)


@pytest.mark.parametrize(
    "ownership",
    ["СОБСТВЕННОСТЬ СУБЪЕКТОВ РОССИЙСКОЙ ФЕДЕРАЦИИ", "МУНИЦИПАЛЬНАЯ СОБСТВЕННОСТЬ"],
)
def test_state_participation_detected(ownership: str) -> None:
    features = by_id(base_features(row(ownership=ownership)))
    assert features["feature:state_capital"].derived is True
    assert "feature:foreign_capital" not in features


@pytest.mark.parametrize(
    ("raw", "city"), [("г. Курган", "Курган"), ("город Омск", "Омск"), ("г.", None)]
)
def test_city_prefix_removed(raw: str, city: str | None) -> None:
    assert base_features(row(city=raw)).location.city == city


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("CLOSE_CLIENT", ClientType.CLOSE_CLIENT), ("PROSPECT", ClientType.PROSPECT), ("???", None)],
)
def test_client_type_mapping(raw: str, expected: ClientType | None) -> None:
    assert base_features(row(client_type=raw)).client_type is expected


def test_features_fit_company_profile_contract() -> None:
    result = base_features(row())
    profile = CompanyProfile(
        company_id="U_1",
        client_type=result.client_type,
        ownership=result.ownership,
        industry=industry("46.38", "Торговля оптовая прочими пищевыми продуктами"),
        scale=result.scale,
        location=result.location,
        foreign_trade=ForeignTrade(has_customs_records=True, countries=["КИТАЙ"]),
        features=result.features,
    )
    assert len(profile.features) == len(result.features)
