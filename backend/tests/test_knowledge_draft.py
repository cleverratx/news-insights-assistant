from datetime import UTC, datetime, timedelta
from decimal import Decimal

import asyncpg

from app.contracts.knowledge import FactSource, FactStatus
from app.contracts.profile import CompanyProfile
from app.data.loader import read_case, store
from app.profile.builder import assemble
from app.profile.graph import CompanyLinks, LinkedCompany, Payment
from app.profile.knowledge import (
    DraftSync,
    confirm,
    delete,
    draft,
    draft_id,
    ensure_schema,
    knowledge_base,
    knowledge_for,
    latest_version,
    revise,
    save,
    sync_draft,
)
from app.profile.row import CompanyRow
from tests.test_data_loader import database
from tests.test_samples import COMPANIES, LINKS

__all__ = ["database"]

T0 = datetime(2026, 10, 21, 9, tzinfo=UTC)
ROW = CompanyRow(
    company_id="U_1",
    okved_code="46.38",
    okved_name="Торговля оптовая рыбой",
    revenue_rub=Decimal("18200000000"),
    employees_fns="16-100",
    region="Москва",
    city="г. Москва",
    client_type="CLIENT",
    has_loan=True,
    has_leasing=True,
    has_customs_records=True,
    customs_countries=["КИТАЙ", "ГОНКОНГ"],
)


def profile(row: CompanyRow = ROW) -> CompanyProfile:
    return assemble(row, CompanyLinks())


def test_draft_covers_case_topics_as_assumptions() -> None:
    facts = {fact.key: fact for fact in draft(profile(), T0)}
    assert set(facts) == {
        "products.industry",
        "company.size",
        "bank.products",
        "constraints.debt",
        "markets.foreign_trade",
        "markets.trade_direction",
        "constraints.sanctions",
        "markets.regions",
    }
    assert all(fact.status is FactStatus.ASSUMPTION for fact in facts.values())
    assert facts["products.industry"].text == (
        "Основная деятельность: Торговля оптовая рыбой (сегмент «Продукты питания и напитки»)"
    )
    assert facts["company.size"].value == "largest"
    assert facts["markets.foreign_trade"].value == "КИТАЙ, ГОНКОНГ"
    assert facts["markets.trade_direction"].value == "import"
    assert facts["bank.products"].evidence == ["feature:loan", "feature:leasing"]
    assert facts["company.size"].id == draft_id("U_1", "company.size")


def test_raw_data_and_our_estimates_have_different_sources() -> None:
    facts = {fact.key: fact for fact in draft(profile(), T0)}
    assert facts["company.size"].source is FactSource.BANK_DATA
    assert facts["markets.foreign_trade"].source is FactSource.BANK_DATA
    assert facts["markets.trade_direction"].source is FactSource.SYSTEM
    assert facts["constraints.debt"].source is FactSource.SYSTEM
    assert facts["constraints.sanctions"].source is FactSource.SYSTEM


def test_every_draft_fact_points_to_profile_features() -> None:
    built = profile()
    known = {feature.id for feature in built.features}
    for fact in draft(built, T0):
        assert fact.evidence and set(fact.evidence) <= known, fact.key


def test_industry_guessed_by_name_is_system_estimate() -> None:
    row = CompanyRow(company_id="U_2", okved_name="Деятельность ресторанов")
    fact = next(item for item in draft(profile(row), T0) if item.key == "products.industry")
    assert fact.source is FactSource.SYSTEM


def test_counterparties_summarised_by_segment() -> None:
    links = CompanyLinks(
        payments=[
            Payment(LinkedCompany("U_7", okved_code="56.10"), "incoming", Decimal(6), 1),
            Payment(LinkedCompany("U_8"), "incoming", Decimal(4), 1),
        ]
    )
    facts = {fact.key: fact for fact in draft(assemble(ROW, links), T0)}
    assert facts["customers.main"].text == (
        "Крупнейшие покупатели по платежам: Гостиницы, общепит и туризм — 60%, "
        "отрасль не известна — 40% оборота"
    )
    assert facts["customers.main"].evidence == [
        "feature:counterparties_incoming",
        "feature:customer_concentration",
    ]
    assert "suppliers.main" not in facts


def test_empty_profile_gives_empty_draft() -> None:
    assert draft(profile(CompanyRow(company_id="U_0")), T0) == []


async def test_sync_respects_client_decisions(database: asyncpg.Connection) -> None:
    await ensure_schema(database)
    assert await sync_draft(database, profile(), T0) == DraftSync(created=8)
    assert await sync_draft(database, profile(), T0) == DraftSync(kept=8)

    size = await latest_version(database, draft_id("U_1", "company.size"))
    await save(database, confirm(size, T0 + timedelta(minutes=1)))
    regions = await latest_version(database, draft_id("U_1", "markets.regions"))
    await save(database, delete(regions, T0 + timedelta(minutes=1)))
    direction = await latest_version(database, draft_id("U_1", "markets.trade_direction"))
    await save(
        database,
        revise(direction, T0, source=FactSource.CLIENT, text="Экспортёр рыбы", value="export"),
    )

    changed = ROW.model_copy(
        update={
            "revenue_rub": Decimal("1000000000"),
            "okved_name": "Торговля оптовая рыбой и морепродуктами",
            "region": "Санкт-Петербург",
            "has_loan": False,
            "has_leasing": False,
            "has_customs_records": False,
            "customs_countries": [],
        }
    )
    result = await sync_draft(database, profile(changed), T0 + timedelta(days=1))
    assert result.removed == 4
    assert result.updated == 1
    base = await knowledge_base(database, "U_1")
    facts = {fact.key: fact for fact in base.facts}
    assert facts["company.size"].source is FactSource.CLIENT
    assert facts["company.size"].text.startswith("Выручка 18,2 млрд")
    assert facts["markets.trade_direction"].value == "export"
    assert "markets.regions" not in facts
    assert "markets.foreign_trade" not in facts
    assert facts["products.industry"].version == 2


async def test_knowledge_for_sample_company(database: asyncpg.Connection) -> None:
    await store(database, read_case(COMPANIES, LINKS))
    base = await knowledge_for(database, "U_900001")
    assert base is not None
    keys = {fact.key for fact in base.facts}
    assert {"customers.main", "suppliers.main", "company.group", "constraints.debt"} <= keys
    customers = next(fact for fact in base.facts if fact.key == "customers.main")
    assert "Гостиницы, общепит и туризм — 70%" in customers.text
    again = await knowledge_for(database, "U_900001")
    assert again == base
    assert await knowledge_for(database, "U_404") is None
