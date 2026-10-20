"""Профиль целиком: на любых данных отвечает, а не падает, и не утверждает того, чего в данных нет.

Отдельные правила модулей проверяются в test_profile_*.py. Здесь — свойства, которые должны
держаться на любой строке: пропуски, неизвестный ОКВЭД, нулевая выручка, пустой граф, мусор.
"""

import random
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from app.contracts.profile import CompanyProfile
from app.data.loader import COMPANY_FIELDS, PAYMENT_FIELDS, PERSON_FIELDS, read_case
from app.profile.builder import Peers, assemble
from app.profile.graph import CompanyLinks, LinkedCompany, Payment
from app.profile.products import PRODUCTS
from app.profile.row import CompanyRow

KNOWN_FIELDS = {*COMPANY_FIELDS.values(), *PERSON_FIELDS.values(), *PAYMENT_FIELDS.values()}
COLUMN_OF = {field: column for column, field in COMPANY_FIELDS.items()}
SAMPLES = Path(__file__).resolve().parents[2] / "data" / "samples"
CASES = 400
# Поля, где непустое значение может оказаться нераспознанным: тогда это тоже пробел.
UNRECOGNIZABLE = {"MAINOKVED_CODE", "CL_TYPE", "CITY"}

# Значения после очистки загрузчиком: пустые строки и «—» к этому моменту уже None.
TEXTS = [None, "x" * 300, "ЧАСТНАЯ СОБСТВЕННОСТЬ", "🙂", "0", "Москва"]
CODES = [None, "46.38", "01.1", "57.10", "99.99", "34.10", "62.01.1", "47", "00.00", "86.10"]
NAMES = [None, "Деятельность ресторанов", "Торговля оптовая", "что-то непонятное"]
REVENUES = [None, Decimal(0), Decimal("0.01"), Decimal(10**6), Decimal(15 * 10**9), Decimal(10**15)]
BANDS = [None, "Менее 15", "16-100", "101-250", "Более 250", "странное"]
CLIENTS = [None, "CLIENT", "CLOSE_CLIENT", "PROSPECT", "client", "VIP"]
FLAGS = [None, True, False]
COUNTRIES = [[], ["КИТАЙ"], ["ЛИТВА", "ГОНКОНГ"], ["НЕИЗВЕСТНИЯ"], ["США", "ОАЭ", "ИНДИЯ"]]


def random_row(rng: random.Random, number: int) -> CompanyRow:
    values: dict[str, Any] = {
        "company_id": f"U_{number}",
        "ownership": rng.choice([*TEXTS, "МУНИЦИПАЛЬНАЯ", "ИНОСТРАННАЯ"]),
        "age_band": rng.choice([*TEXTS, "8-15"]),
        "lifecycle_stage": rng.choice(TEXTS),
        "okved_code": rng.choice(CODES),
        "okved_name": rng.choice(NAMES),
        "has_key_owner_mark": rng.choice(FLAGS),
        "employees_fns": rng.choice(BANDS),
        "employees_spark": rng.choice(BANDS),
        "client_type": rng.choice(CLIENTS),
        "customs_countries": rng.choice(COUNTRIES),
        "revenue_rub": rng.choice(REVENUES),
        "group_id": rng.choice([None, "g_1"]),
        "city": rng.choice([*TEXTS, "г.", "г. Омск"]),
        "region": rng.choice(TEXTS),
    }
    for _, column, _ in PRODUCTS:
        values[column] = rng.choice(FLAGS)
    values["has_customs_records"] = rng.choice(FLAGS)
    return CompanyRow(**values)


def random_company(rng: random.Random, number: int) -> LinkedCompany:
    return LinkedCompany(
        company_id=f"U_{number}",
        okved_code=rng.choice(CODES),
        okved_name=rng.choice(NAMES),
        region=rng.choice(TEXTS),
        revenue_rub=rng.choice(REVENUES),
    )


def random_links(rng: random.Random) -> CompanyLinks:
    return CompanyLinks(
        group_members=[random_company(rng, 1000 + i) for i in range(rng.randint(0, 5))],
        payments=[
            Payment(
                partner=random_company(rng, 2000 + i),
                direction=rng.choice(["incoming", "outgoing"]),
                amount_rub=rng.choice([Decimal(0), Decimal("0.01"), Decimal(10**9)]),
                payments_count=rng.choice([None, 0, 7]),
            )
            for i in range(rng.randint(0, 25))
        ],
        person_regions=[rng.choice(TEXTS) for _ in range(rng.randint(0, 4))],
        shared_person_companies=[random_company(rng, 3000)] if rng.random() < 0.3 else [],
        shared_person_total=rng.randint(0, 3),
    )


def random_peers(rng: random.Random) -> Peers | None:
    count = rng.randint(1, 500)
    total = rng.choice([Decimal(0), Decimal(10**12)])
    return rng.choice([None, Peers("okved_code", count, rng.randint(1, count), total)])


def empty(value: object) -> bool:
    return value is None or value == [] or value == 0 or value == ""


def check_facts(row: CompanyRow, profile: CompanyProfile) -> None:
    """Профиль не утверждает того, чего нет в строке выгрузки."""
    assert CompanyProfile.model_validate_json(profile.model_dump_json()) == profile
    for feature in profile.features:
        assert feature.source_fields, feature.id
        assert set(feature.source_fields) <= KNOWN_FIELDS, feature.id
        if not feature.derived:
            for source in feature.source_fields:
                if source in COLUMN_OF and source != "FTS_FLAG":
                    assert not empty(getattr(row, COLUMN_OF[source])), (feature.id, source)
    features = {feature.id: feature for feature in profile.features}

    for product, column, _ in PRODUCTS:
        assert (f"feature:{product.value}" in features) == (getattr(row, column) is True)
        assert (product in profile.products) == (getattr(row, column) is True)

    if row.revenue_rub:
        assert features["feature:revenue"].value == float(row.revenue_rub)
        assert profile.scale.is_large_business == (row.revenue_rub >= 15 * 10**9)
    else:
        assert "feature:revenue" not in features
        assert "feature:size" not in features
        assert profile.scale.is_large_business is None

    for field in profile.data_quality.missing_fields:
        assert field in KNOWN_FIELDS
        if field in COLUMN_OF and field not in UNRECOGNIZABLE:
            assert empty(getattr(row, COLUMN_OF[field])), field

    for direction in ("incoming", "outgoing"):
        shares = [item.share or 0 for item in profile.counterparties if item.direction == direction]
        assert sum(shares) <= 1 + 1e-9

    topics = [exposure.topic for exposure in profile.exposures if exposure.topic != "industry"]
    assert len(topics) == len(set(topics))


@pytest.mark.parametrize("seed", range(4))
def test_random_rows_and_links_never_break_the_profile(seed: int) -> None:
    rng = random.Random(seed)
    for number in range(CASES // 4):
        row = random_row(rng, number)
        profile = assemble(row, random_links(rng), random_peers(rng))
        check_facts(row, profile)


def test_all_gaps() -> None:
    row = CompanyRow(company_id="U_1")
    profile = assemble(row, CompanyLinks())
    check_facts(row, profile)
    assert profile.features == []
    assert profile.exposures == []
    assert profile.industry.segment == "other"
    assert profile.scale.size_category is None
    assert profile.group is None
    assert len(profile.data_quality.missing_fields) >= 10


@pytest.mark.parametrize(("code", "name"), [("57.10", None), ("abc", "непонятно"), (None, None)])
def test_unknown_okved(code: str | None, name: str | None) -> None:
    row = CompanyRow(company_id="U_1", okved_code=code, okved_name=name, has_loan=True)
    profile = assemble(row, CompanyLinks())
    check_facts(row, profile)
    assert profile.industry.resolved_by == "unknown"
    assert profile.industry.keywords == []
    assert "feature:industry" not in {feature.id for feature in profile.features}
    assert "MAINOKVED_CODE" in profile.data_quality.missing_fields
    assert any(exposure.topic == "key_rate" for exposure in profile.exposures)


def test_zero_revenue_is_not_a_size_or_rank() -> None:
    row = CompanyRow(company_id="U_1", okved_code="46.38", revenue_rub=Decimal(0))
    profile = assemble(row, CompanyLinks())
    check_facts(row, profile)
    assert profile.scale.size_category is None
    assert profile.market_position is None
    assert any("нулю" in warning for warning in profile.data_quality.warnings)


def test_empty_graph() -> None:
    row = CompanyRow(company_id="U_1", okved_code="46.38", group_id="g_1")
    profile = assemble(row, CompanyLinks())
    check_facts(row, profile)
    assert profile.group is None
    assert profile.counterparties == []
    assert profile.related_companies == []
    assert profile.linked_persons_count == 0
    assert "Платежей нет: контрагенты не определены" in profile.data_quality.warnings


def test_graph_partners_unknown_to_the_dataset() -> None:
    row = CompanyRow(company_id="U_1", okved_code="46.38")
    links = CompanyLinks(
        payments=[
            Payment(LinkedCompany("U_404"), "incoming", Decimal(100), None),
            Payment(LinkedCompany("U_405"), "outgoing", Decimal(0), 1),
        ]
    )
    profile = assemble(row, links)
    check_facts(row, profile)
    assert [item.company_id for item in profile.counterparties] == ["U_404"]
    assert profile.counterparties[0].segment is None
    assert all(exposure.topic != "industry" for exposure in profile.exposures)


def test_every_sample_row_holds_the_facts() -> None:
    data = read_case(SAMPLES / "df_Uid_data_hack.csv", SAMPLES / "df_Fid_Uid_link_hack.csv")
    for record in data.companies.values():
        row = CompanyRow.from_record(record)
        check_facts(row, assemble(row, CompanyLinks()))
