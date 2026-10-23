import json
import os
from collections.abc import AsyncIterator
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

from app.core.db import postgres_dsn
from app.data import clean
from app.data.clean import Cell, Issues
from app.data.loader import COMPANY_FIELDS, read_case, store
from app.data.reader import CaseFileError, open_csv

HEADER = ";".join(COMPANY_FIELDS.values())
COMPANY = {
    "SYNTHETIC_U": "U_1",
    "ORG_TYPE": "КОММ_ОРГ",
    "OKFS_NAME": "ЧАСТНАЯ СОБСТВЕННОСТЬ",
    "COMPANY_AGE_YEARS_GRADATION": "8-15",
    "ECO_MARK_LABEL": "Оптимизация",
    "MAINOKVED_CODE": "46.38",
    "MAINOKVED_NAME": "Торговля оптовая пищевыми продуктами",
    "NEGATIVEMARK": "0.0",
    "AMOUNT_WORKERS_FNS_GRADATION": "16-100",
    "AMOUNT_WORKERS_SPARK_GRADATION": "",
    "CL_TYPE": "CLIENT",
    "CL_PRODUCT_RKO_MARK": "1.0",
    "CL_PRODUCT_GAR_MARK": "0.0",
    "CL_PRODUCT_LOA_MARK": "1.0",
    "CL_PRODUCT_LEA_MARK": "0.0",
    "CL_PRODUCT_DEP_MARK": "0.0",
    "CL_PRODUCT_ZPP_MARK": "0.0",
    "FTS_FLAG": "1.0",
    "FTS_COUNTRY_NAME": "КИТАЙ, ТУРЦИЯ",
    "FACTORING_MARK": "0.0",
    "REVENUE_COMPANY": "18200000000.0",
    "GROUP_ID": "group_1_sb",
    "CITY": "г. Москва",
    "REGION": "Москва",
}


def company_line(**changes: str) -> str:
    return ";".join({**COMPANY, **changes}.values())


def write(path: Path, lines: list[str], encoding: str = "utf-8-sig") -> Path:
    path.write_text("\n".join(lines) + "\n", encoding=encoding)
    return path


def cell(raw: str | None, issues: Issues | None = None) -> Cell:
    return Cell(raw, "companies", "FIELD", 2, issues or Issues())


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("  г. Москва ", "г. Москва"), ("nan", None), ("—", None), ("", None), (None, None)],
)
def test_text_trims_spaces_and_null_tokens(raw: str | None, expected: str | None) -> None:
    assert clean.text(cell(raw)) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("1.0", True), ("1,0", True), ("да", True), ("0", False), ("нет", False), ("", None)],
)
def test_mark_understands_case_variants(raw: str, expected: bool | None) -> None:
    assert clean.mark(cell(raw)) is expected


def test_unknown_mark_is_counted_not_guessed() -> None:
    issues = Issues()
    assert clean.mark(cell("возможно", issues)) is None
    assert issues.summary()[0]["problem"] == "флаг не распознан"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("18200000000.0", Decimal("18200000000.00")),
        ("1 234 567,89", Decimal("1234567.89")),
        ("1.234.567,5", Decimal("1234567.50")),
        ("1,234,567.5", Decimal("1234567.50")),
        ("1.4e9", Decimal("1400000000.00")),
        ("500 руб.", Decimal("500.00")),
    ],
)
def test_amount_parses_number_formats(raw: str, expected: Decimal) -> None:
    assert clean.amount(cell(raw)) == expected


@pytest.mark.parametrize(
    ("raw", "problem"), [("-5", "отрицательное значение"), ("абв", "не число"), ("inf", "не число")]
)
def test_bad_amount_becomes_empty_with_issue(raw: str, problem: str) -> None:
    issues = Issues()
    assert clean.amount(cell(raw, issues)) is None
    assert issues.summary()[0]["problem"] == problem


@pytest.mark.parametrize("raw", ["1000", "1000.0", "1 000,00", "1000 руб."])
def test_revenue_stub_becomes_empty_with_issue(raw: str) -> None:
    issues = Issues()
    assert clean.revenue(cell(raw, issues)) is None
    assert issues.summary()[0]["problem"] == "заглушка 1000 ₽ вместо выручки, считаем неизвестной"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("999", Decimal("999.00")), ("1000.5", Decimal("1000.50")), ("10000", Decimal("10000.00"))],
)
def test_revenue_near_stub_is_kept(raw: str, expected: Decimal) -> None:
    issues = Issues()
    assert clean.revenue(cell(raw, issues)) == expected
    assert issues.summary() == []


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("46.38", "46.38"), ("46,38", "46.38"), ("1.1", "01.1"), ("62.01.", "62.01"), ("X1", None)],
)
def test_okved_normalized(raw: str, expected: str | None) -> None:
    assert clean.okved(cell(raw)) == expected


def test_countries_split_and_deduplicated() -> None:
    assert clean.countries(cell("китай, Турция; КИТАЙ /nan")) == ["КИТАЙ", "ТУРЦИЯ"]


def test_count_rejects_fractions() -> None:
    issues = Issues()
    assert clean.count(cell("3.5", issues)) is None
    assert clean.count(cell("12,0")) == 12


def test_reader_detects_cp1251_and_comma(tmp_path: Path) -> None:
    path = write(tmp_path / "c.csv", ["synthetic_u,region", "U_1,Москва"], encoding="cp1251")
    csv_file = open_csv(path)
    assert (csv_file.encoding, csv_file.delimiter) == ("cp1251", ",")
    assert csv_file.columns == ["SYNTHETIC_U", "REGION"]
    assert next(csv_file.rows()).values == {"SYNTHETIC_U": "U_1", "REGION": "Москва"}


def test_reader_reports_missing_file(tmp_path: Path) -> None:
    with pytest.raises(CaseFileError, match="не найден"):
        open_csv(tmp_path / "nope.csv")


def test_companies_without_id_column_rejected(tmp_path: Path) -> None:
    path = write(tmp_path / "c.csv", ["REGION;CITY", "Москва;г. Москва"])
    with pytest.raises(CaseFileError, match="SYNTHETIC_U"):
        read_case(path, None)


def test_noisy_companies_are_cleaned(tmp_path: Path) -> None:
    companies = write(
        tmp_path / "companies.csv",
        [
            HEADER,
            company_line(),
            company_line(SYNTHETIC_U="U_2", REVENUE_COMPANY="-1", CL_TYPE="кто-то", FTS_FLAG="0"),
            company_line(SYNTHETIC_U=""),
            company_line(SYNTHETIC_U="U_1", REGION="", CITY="", GROUP_ID=""),
            company_line(SYNTHETIC_U="U_3") + ";лишнее",
            "U_4;КОММ_ОРГ",
            "",
        ],
    )
    data = read_case(companies, None)
    report = data.report()

    assert set(data.companies) == {"U_1", "U_2", "U_4"}
    first = data.companies["U_1"]
    assert first["region"] == "Москва"
    assert first["has_loan"] is True
    assert first["customs_countries"] == ["КИТАЙ", "ТУРЦИЯ"]
    assert first["revenue_rub"] == Decimal("18200000000.00")
    second = data.companies["U_2"]
    assert second["revenue_rub"] is None
    assert second["client_type"] is None
    assert data.companies["U_4"]["region"] is None

    assert report["tables"]["companies"] == {"read": 6, "loaded": 3, "rejected": 2, "merged": 1}
    problems = {(item["column"], item["problem"]) for item in report["issues"]}
    assert ("REVENUE_COMPANY", "отрицательное значение") in problems
    assert ("CL_TYPE", "значение вне справочника") in problems
    assert ("FTS_COUNTRY_NAME", "страны ФТС при FTS_FLAG = 0") in problems
    assert ("SYNTHETIC_U", "дубликат, оставлена полная строка") in problems
    assert ("*", "лишние поля в строке, строка отброшена") in problems
    assert ("*", "не хватает полей в строке") in problems
    assert "Платежей нет" in report["warnings"][0]


def test_revenue_stub_is_unknown_but_company_kept(tmp_path: Path) -> None:
    companies = write(
        tmp_path / "c.csv",
        [HEADER, company_line(), company_line(SYNTHETIC_U="U_2", REVENUE_COMPANY="1000.0")],
    )
    links = write(
        tmp_path / "l.csv",
        [
            "SYNTHETIC_F;CITY_NAME;RC_NAME;SYNTHETIC_U;BENEFICIARY;PAYER;PAY_AMT;PAY_CNT",
            ";;;;U_1;U_2;1000;1",
        ],
    )
    data = read_case(companies, links)
    report = data.report()

    assert data.companies["U_2"]["revenue_rub"] is None
    assert data.companies["U_1"]["revenue_rub"] == Decimal("18200000000.00")
    assert report["tables"]["companies"]["loaded"] == 2
    assert data.payments[("U_2", "U_1")]["amount_rub"] == Decimal("1000.00")
    stubs = [item for item in report["issues"] if item["column"] == "REVENUE_COMPANY"]
    assert [(item["problem"], item["count"]) for item in stubs] == [
        ("заглушка 1000 ₽ вместо выручки, считаем неизвестной", 1)
    ]


def test_report_has_no_raw_identifiers(tmp_path: Path) -> None:
    companies = write(tmp_path / "c.csv", [HEADER, company_line(SYNTHETIC_U="U_777", CL_TYPE="?")])
    report = json.dumps(read_case(companies, None).report(), ensure_ascii=False)
    assert "U_777" not in report
    assert str(tmp_path) not in report


def test_persons_and_payments_from_links(tmp_path: Path) -> None:
    companies = write(tmp_path / "c.csv", [HEADER, company_line()])
    links = write(
        tmp_path / "links.csv",
        [
            "SYNTHETIC_F;CITY_NAME;RC_NAME;SYNTHETIC_U;BENEFICIARY;PAYER;PAY_AMT;PAY_CNT",
            "F_1;Москва;МОСКОВСКИЙ;U_1;;;;",
            "F_1;Москва;МОСКОВСКИЙ;U_1;;;;",
            "F_2;;;;;;;",
            ";;;;U_1;U_9;1 000,50;2",
            ";;;;U_1;U_9;500;1",
            ";;;;U_1;U_1;10;1",
            ";;;;U_1;U_8;;1",
        ],
    )
    data = read_case(companies, links)
    report = data.report()
    assert list(data.persons) == [("F_1", "U_1")]
    assert report["tables"]["persons"] == {"read": 3, "loaded": 1, "rejected": 1, "merged": 1}
    payment = data.payments[("U_9", "U_1")]
    assert payment["amount_rub"] == Decimal("1500.50")
    assert payment["payments_count"] == 3
    assert report["tables"]["payments"] == {"read": 4, "loaded": 1, "rejected": 2, "merged": 1}
    assert report["warnings"] == []


def test_links_without_payments_columns(tmp_path: Path) -> None:
    companies = write(tmp_path / "c.csv", [HEADER, company_line()])
    links = write(tmp_path / "l.csv", ["SYNTHETIC_F;CITY_NAME;RC_NAME;SYNTHETIC_U", "F_1;;;U_1"])
    data = read_case(companies, links)
    assert len(data.persons) == 1
    assert data.payments == {}


def test_postgres_dsn_drops_driver() -> None:
    assert postgres_dsn("postgresql+asyncpg://a:b@h:5432/d") == "postgresql://a:b@h:5432/d"


@pytest.fixture
async def database() -> AsyncIterator[asyncpg.Connection]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Для интеграционных тестов нужен TEST_DATABASE_URL тестового PostgreSQL")
    admin = await asyncpg.connect(postgres_dsn(url))
    schema = f"test_{uuid4().hex}"
    await admin.execute(f'CREATE SCHEMA "{schema}"')
    connection = await asyncpg.connect(postgres_dsn(url), server_settings={"search_path": schema})
    try:
        yield connection
    finally:
        await connection.close()
        await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await admin.close()


async def test_store_reloads_tables_atomically(
    database: asyncpg.Connection, tmp_path: Path
) -> None:
    companies = write(tmp_path / "c.csv", [HEADER, company_line(), company_line(SYNTHETIC_U="U_2")])
    links = write(
        tmp_path / "l.csv",
        [
            "SYNTHETIC_F;SYNTHETIC_U;PAYER;BENEFICIARY;PAY_AMT;PAY_CNT",
            "F_1;U_1;;;;",
            ";;U_2;U_1;10;1",
        ],
    )
    await store(database, read_case(companies, links))
    await store(database, read_case(companies, links))

    row = await database.fetchrow("SELECT * FROM companies WHERE company_id = 'U_1'")
    assert row["revenue_rub"] == Decimal("18200000000.00")
    assert row["customs_countries"] == ["КИТАЙ", "ТУРЦИЯ"]
    assert row["has_loan"] is True
    assert await database.fetchval("SELECT count(*) FROM companies") == 2
    assert await database.fetchval("SELECT count(*) FROM persons") == 1
    assert await database.fetchval("SELECT amount_rub FROM payments") == Decimal("10.00")
    loads = await database.fetch("SELECT report FROM data_loads ORDER BY id")
    assert len(loads) == 2
    assert json.loads(loads[-1]["report"])["tables"]["companies"]["loaded"] == 2
