from decimal import Decimal
from pathlib import Path
from typing import Any

import asyncpg

from app.contracts.news import Topic
from app.contracts.profile import CompanyProfile, ForeignTrade, Location, Scale
from app.data.loader import read_case, store
from app.profile.graph import CompanyLinks, LinkedCompany, Payment, fetch_links, link_profile
from app.profile.okved import industry
from app.profile.row import CompanyRow
from tests.test_data_loader import HEADER, company_line, database, write

__all__ = ["database"]

CLIENT = CompanyRow(
    company_id="U_1", okved_code="46.38", okved_name="Оптовая торговля рыбой", group_id="g_1"
)


def company(company_id: str, code: str | None, revenue: str | None = None) -> LinkedCompany:
    return LinkedCompany(
        company_id=company_id,
        okved_code=code,
        region="Москва",
        revenue_rub=Decimal(revenue) if revenue else None,
    )


def features(result: Any) -> dict[str, Any]:
    return {feature.id: feature for feature in result.features}


def test_group_members_segments_and_exposures() -> None:
    links = CompanyLinks(
        group_members=[
            company("U_2", "52.10", "500"),
            company("U_3", "52.29", "900"),
            company("U_4", "46.31", None),
            company("U_5", None),
        ]
    )
    result = link_profile(CLIENT, links)

    assert result.group is not None
    assert result.group.companies_count == 5
    assert result.group.industries == ["Транспорт и логистика", "Продукты питания и напитки"]
    assert [member.company_id for member in result.group.members] == ["U_3", "U_2", "U_4", "U_5"]
    assert result.group.members[0].segment == "logistics"
    found = features(result)
    assert found["feature:group"].label == "Входит в группу из 5 компаний"
    assert found["feature:group"].source_fields == ["GROUP_ID"]
    assert "«Транспорт и логистика»" in found["feature:group_segments"].label
    exposure = result.exposures[0]
    assert exposure.topic is Topic.INDUSTRY
    assert exposure.feature_ids == ["feature:group_segments"]
    assert "логистика" in exposure.keywords
    assert len(result.exposures) == 1


def test_single_company_group_is_not_a_group() -> None:
    result = link_profile(CLIENT, CompanyLinks())
    assert result.group is None
    assert result.features == []
    assert result.warnings == ["Платежей нет: контрагенты не определены"]


def test_missing_group_id_is_a_gap() -> None:
    result = link_profile(CompanyRow(company_id="U_1"), CompanyLinks())
    assert result.missing_fields == ["GROUP_ID"]


def test_counterparties_ranked_with_shares_and_segments() -> None:
    links = CompanyLinks(
        payments=[
            Payment(company("U_10", "47.11"), "incoming", Decimal("600"), 6),
            Payment(company("U_11", "56.10"), "incoming", Decimal("350"), 3),
            Payment(company("U_12", "62.01"), "incoming", Decimal("50"), 1),
            Payment(company("U_13", "49.41"), "outgoing", Decimal("1000"), None),
            Payment(LinkedCompany("U_99"), "outgoing", Decimal("0"), 1),
        ]
    )
    result = link_profile(CLIENT, links)

    incoming = [item for item in result.counterparties if item.direction == "incoming"]
    assert [item.company_id for item in incoming] == ["U_10", "U_11", "U_12"]
    assert incoming[0].share == 0.6
    assert incoming[0].segment == "food"
    outgoing = [item for item in result.counterparties if item.direction == "outgoing"]
    assert outgoing[0].payments_count is None
    assert outgoing[1].segment is None

    found = features(result)
    assert found["feature:counterparties_incoming"].label == (
        "Покупатели по платежам: 3, крупнейший — 60% оборота"
    )
    assert found["feature:counterparties_incoming"].source_fields == [
        "PAYER",
        "BENEFICIARY",
        "PAY_AMT",
        "PAY_CNT",
    ]
    reasons = [item.reason for item in result.exposures]
    assert reasons == [
        "Ваши покупатели из отрасли «Продукты питания и напитки» дают 60% оборота: "
        "спад на их рынке ударит по вашей выручке",
        "Ваши покупатели из отрасли «Гостиницы, общепит и туризм» дают 35% оборота: "
        "спад на их рынке ударит по вашей выручке",
        "Ваши поставщики из отрасли «Транспорт и логистика» дают 100% оборота: "
        "рост цен и перебои у них отразятся на ваших затратах",
    ]
    assert result.warnings == []


def test_persons_regions_and_shared_links() -> None:
    links = CompanyLinks(
        person_regions=["МОСКОВСКИЙ", None, "МОСКОВСКИЙ", "СИБИРСКИЙ"],
        shared_person_companies=[company("U_7", "68.20")],
        shared_person_total=25,
    )
    result = link_profile(CLIENT, links)
    assert result.linked_persons_count == 4
    assert result.person_regions == ["МОСКОВСКИЙ", "СИБИРСКИЙ"]
    assert result.related_companies[0].relation == "shared_person"
    assert result.related_companies[0].segment == "real_estate"
    found = features(result)
    assert found["feature:persons"].label == ("Связанных физлиц: 4, регионы: МОСКОВСКИЙ, СИБИРСКИЙ")
    assert found["feature:person_links"].value == 25


def test_link_profile_fits_contract() -> None:
    result = link_profile(
        CLIENT,
        CompanyLinks(
            group_members=[company("U_2", "52.10")],
            payments=[Payment(company("U_10", "47.11"), "incoming", Decimal("10"), 1)],
            person_regions=["МОСКОВСКИЙ"],
        ),
    )
    profile = CompanyProfile(
        company_id="U_1",
        industry=industry("46.38"),
        scale=Scale(),
        location=Location(),
        foreign_trade=ForeignTrade(has_customs_records=False),
        group=result.group,
        counterparties=result.counterparties,
        linked_persons_count=result.linked_persons_count,
        person_regions=result.person_regions,
        related_companies=result.related_companies,
        features=result.features,
        exposures=result.exposures,
    )
    assert profile.group is not None


async def test_fetch_links_from_loaded_tables(database: asyncpg.Connection, tmp_path: Path) -> None:
    companies = write(
        tmp_path / "c.csv",
        [
            HEADER,
            company_line(SYNTHETIC_U="U_1", GROUP_ID="g_1"),
            company_line(SYNTHETIC_U="U_2", GROUP_ID="g_1", MAINOKVED_CODE="52.10"),
            company_line(SYNTHETIC_U="U_3", GROUP_ID="g_2", MAINOKVED_CODE="47.11"),
            company_line(SYNTHETIC_U="U_4", GROUP_ID="g_3", MAINOKVED_CODE="68.20"),
        ],
    )
    links_file = write(
        tmp_path / "l.csv",
        [
            "SYNTHETIC_F;RC_NAME;SYNTHETIC_U;PAYER;BENEFICIARY;PAY_AMT;PAY_CNT",
            "F_1;МОСКОВСКИЙ;U_1;;;;",
            "F_1;МОСКОВСКИЙ;U_4;;;;",
            ";;;U_3;U_1;700;7",
            ";;;U_1;U_9;300;3",
        ],
    )
    await store(database, read_case(companies, links_file))
    record = await database.fetchrow("SELECT * FROM companies WHERE company_id = 'U_1'")

    links = await fetch_links(database, CompanyRow.from_record(record))

    assert [member.company_id for member in links.group_members] == ["U_2"]
    incoming = [payment for payment in links.payments if payment.direction == "incoming"]
    outgoing = [payment for payment in links.payments if payment.direction == "outgoing"]
    assert incoming[0].partner.okved_code == "47.11"
    assert incoming[0].amount_rub == Decimal("700.00")
    assert outgoing[0].partner.company_id == "U_9"
    assert outgoing[0].partner.okved_code is None
    assert links.person_regions == ["МОСКОВСКИЙ"]
    assert [item.company_id for item in links.shared_person_companies] == ["U_4"]
    assert links.shared_person_total == 1
    result = link_profile(CompanyRow.from_record(record), links)
    assert result.group is not None and result.group.companies_count == 2
