import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app.data import clean
from app.data.clean import Cell, Issues
from app.data.reader import CaseFileError, CsvFile, CsvRow, open_csv

CLIENT_TYPES = {"CLIENT", "CLOSE_CLIENT", "PROSPECT"}

# Столбец таблицы → поле выгрузки. По этой карте профиль ссылается на исходное поле.
COMPANY_FIELDS = {
    "company_id": "SYNTHETIC_U",
    "org_type": "ORG_TYPE",
    "ownership": "OKFS_NAME",
    "age_band": "COMPANY_AGE_YEARS_GRADATION",
    "lifecycle_stage": "ECO_MARK_LABEL",
    "okved_code": "MAINOKVED_CODE",
    "okved_name": "MAINOKVED_NAME",
    "has_key_owner_mark": "NEGATIVEMARK",
    "employees_fns": "AMOUNT_WORKERS_FNS_GRADATION",
    "employees_spark": "AMOUNT_WORKERS_SPARK_GRADATION",
    "client_type": "CL_TYPE",
    "has_settlement": "CL_PRODUCT_RKO_MARK",
    "has_guarantee": "CL_PRODUCT_GAR_MARK",
    "has_loan": "CL_PRODUCT_LOA_MARK",
    "has_leasing": "CL_PRODUCT_LEA_MARK",
    "has_deposit": "CL_PRODUCT_DEP_MARK",
    "has_payroll": "CL_PRODUCT_ZPP_MARK",
    "has_customs_records": "FTS_FLAG",
    "customs_countries": "FTS_COUNTRY_NAME",
    "has_factoring": "FACTORING_MARK",
    "revenue_rub": "REVENUE_COMPANY",
    "group_id": "GROUP_ID",
    "city": "CITY",
    "region": "REGION",
}
PERSON_FIELDS = {
    "person_id": "SYNTHETIC_F",
    "company_id": "SYNTHETIC_U",
    "city": "CITY_NAME",
    "region": "RC_NAME",
}
PAYMENT_FIELDS = {
    "payer_id": "PAYER",
    "beneficiary_id": "BENEFICIARY",
    "amount_rub": "PAY_AMT",
    "payments_count": "PAY_CNT",
}

Parser = Callable[[Cell], object]
MARK_COLUMNS = {name for name in COMPANY_FIELDS if name.startswith("has_")}
COMPANY_PARSERS: dict[str, Parser] = {
    **{name: clean.mark for name in MARK_COLUMNS},
    "okved_code": clean.okved,
    "client_type": lambda cell: clean.choice(cell, CLIENT_TYPES),
    "customs_countries": clean.countries,
    "revenue_rub": clean.revenue,
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    company_id text PRIMARY KEY,
    org_type text,
    ownership text,
    age_band text,
    lifecycle_stage text,
    okved_code text,
    okved_name text,
    has_key_owner_mark boolean,
    employees_fns text,
    employees_spark text,
    client_type text,
    has_settlement boolean,
    has_guarantee boolean,
    has_loan boolean,
    has_leasing boolean,
    has_deposit boolean,
    has_payroll boolean,
    has_customs_records boolean,
    customs_countries text[] NOT NULL DEFAULT '{}',
    has_factoring boolean,
    revenue_rub numeric(20, 2),
    group_id text,
    city text,
    region text,
    source_line integer NOT NULL
);
CREATE INDEX IF NOT EXISTS companies_group_id ON companies (group_id);
CREATE INDEX IF NOT EXISTS companies_okved_code ON companies (okved_code);
CREATE INDEX IF NOT EXISTS companies_region ON companies (region);

CREATE TABLE IF NOT EXISTS persons (
    person_id text NOT NULL,
    company_id text NOT NULL,
    city text,
    region text,
    PRIMARY KEY (person_id, company_id)
);
CREATE INDEX IF NOT EXISTS persons_company_id ON persons (company_id);

CREATE TABLE IF NOT EXISTS payments (
    payer_id text NOT NULL,
    beneficiary_id text NOT NULL,
    amount_rub numeric(20, 2) NOT NULL,
    payments_count integer,
    PRIMARY KEY (payer_id, beneficiary_id)
);
CREATE INDEX IF NOT EXISTS payments_beneficiary_id ON payments (beneficiary_id);

CREATE TABLE IF NOT EXISTS data_loads (
    id serial PRIMARY KEY,
    loaded_at timestamptz NOT NULL DEFAULT now(),
    report jsonb NOT NULL
);
"""

Record = dict[str, Any]


@dataclass
class TableStats:
    read: int = 0
    loaded: int = 0
    rejected: int = 0
    merged: int = 0


@dataclass
class CaseData:
    companies: dict[str, Record] = field(default_factory=dict)
    persons: dict[tuple[str, str], Record] = field(default_factory=dict)
    payments: dict[tuple[str, str], Record] = field(default_factory=dict)
    issues: Issues = field(default_factory=Issues)
    stats: dict[str, TableStats] = field(
        default_factory=lambda: {
            name: TableStats() for name in ("companies", "persons", "payments")
        }
    )
    files: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def report(self) -> dict[str, object]:
        for name, rows in (
            ("companies", self.companies),
            ("persons", self.persons),
            ("payments", self.payments),
        ):
            self.stats[name].loaded = len(rows)
        return {
            "files": self.files,
            "tables": {name: asdict(stats) for name, stats in self.stats.items()},
            "warnings": self.warnings,
            "issues": self.issues.summary(),
        }


def cells(row: CsvRow, table: str, issues: Issues) -> Callable[[str], Cell]:
    def cell(column: str) -> Cell:
        return Cell(row.values.get(column), table, column, row.line, issues)

    return cell


def well_formed(row: CsvRow, csv_file: CsvFile, table: str, data: CaseData) -> bool:
    if row.width > len(csv_file.columns):
        data.issues.add(table, "*", "лишние поля в строке, строка отброшена", row.line)
        data.stats[table].rejected += 1
        return False
    if row.width < len(csv_file.columns):
        data.issues.add(table, "*", "не хватает полей в строке", row.line)
    return True


def note_missing_columns(csv_file: CsvFile, expected: dict[str, str], data: CaseData) -> None:
    missing = [column for column in expected.values() if column not in csv_file.columns]
    if missing:
        data.warnings.append(f"{csv_file.path.name}: нет столбцов {', '.join(missing)}")


def blank(row: CsvRow, *columns: str) -> bool:
    return all(not (row.values.get(column) or "").strip() for column in columns)


def filled(record: Record) -> int:
    return sum(1 for value in record.values() if value not in (None, []))


def read_companies(csv_file: CsvFile, data: CaseData) -> None:
    if COMPANY_FIELDS["company_id"] not in csv_file.columns:
        raise CaseFileError(f"{csv_file.path.name}: нет столбца SYNTHETIC_U")
    note_missing_columns(csv_file, COMPANY_FIELDS, data)
    stats = data.stats["companies"]
    for row in csv_file.rows():
        stats.read += 1
        if not well_formed(row, csv_file, "companies", data):
            continue
        cell = cells(row, "companies", data.issues)
        record: Record = {
            name: COMPANY_PARSERS.get(name, clean.text)(cell(source))
            for name, source in COMPANY_FIELDS.items()
        }
        record["source_line"] = row.line
        company_id = record["company_id"]
        if company_id is None:
            data.issues.add(
                "companies", "SYNTHETIC_U", "нет идентификатора, строка отброшена", row.line
            )
            stats.rejected += 1
            continue
        if record["customs_countries"] and record["has_customs_records"] is False:
            cell("FTS_COUNTRY_NAME").problem("страны ФТС при FTS_FLAG = 0")
        known = data.companies.get(company_id)
        if known is not None:
            stats.merged += 1
            data.issues.add(
                "companies", "SYNTHETIC_U", "дубликат, оставлена полная строка", row.line
            )
            if filled(record) <= filled(known):
                continue
        data.companies[company_id] = record


def read_persons(csv_file: CsvFile, data: CaseData) -> None:
    stats = data.stats["persons"]
    for row in csv_file.rows():
        if blank(row, "SYNTHETIC_F", "SYNTHETIC_U"):
            continue
        stats.read += 1
        if not well_formed(row, csv_file, "persons", data):
            continue
        cell = cells(row, "persons", data.issues)
        record: Record = {name: clean.text(cell(source)) for name, source in PERSON_FIELDS.items()}
        if record["person_id"] is None or record["company_id"] is None:
            data.issues.add("persons", "*", "нет идентификатора, строка отброшена", row.line)
            stats.rejected += 1
            continue
        key = (record["person_id"], record["company_id"])
        if key in data.persons:
            stats.merged += 1
            continue
        data.persons[key] = record


def read_payments(csv_file: CsvFile, data: CaseData) -> None:
    stats = data.stats["payments"]
    for row in csv_file.rows():
        if blank(row, "PAYER", "BENEFICIARY"):
            continue
        stats.read += 1
        if not well_formed(row, csv_file, "payments", data):
            continue
        cell = cells(row, "payments", data.issues)
        payer = clean.text(cell("PAYER"))
        beneficiary = clean.text(cell("BENEFICIARY"))
        paid = clean.amount(cell("PAY_AMT"))
        times = clean.count(cell("PAY_CNT"))
        problem = None
        if payer is None or beneficiary is None:
            problem = "нет плательщика или получателя"
        elif payer == beneficiary:
            problem = "платёж самому себе"
        elif paid is None:
            problem = "нет суммы"
        if problem is not None:
            data.issues.add("payments", "*", f"{problem}, строка отброшена", row.line)
            stats.rejected += 1
            continue
        assert payer is not None and beneficiary is not None and paid is not None
        known = data.payments.get((payer, beneficiary))
        if known is None:
            data.payments[(payer, beneficiary)] = {
                "payer_id": payer,
                "beneficiary_id": beneficiary,
                "amount_rub": paid,
                "payments_count": times,
            }
            continue
        stats.merged += 1
        known["amount_rub"] += paid
        if times is not None:
            known["payments_count"] = (known["payments_count"] or 0) + times


def read_case(companies: Path, links: Path | None, payments: Path | None = None) -> CaseData:
    data = CaseData()
    company_file = open_csv(companies)
    data.files.append(company_file.path.name)
    read_companies(company_file, data)
    if links is not None:
        link_file = open_csv(links)
        data.files.append(link_file.path.name)
        if "SYNTHETIC_F" in link_file.columns:
            read_persons(link_file, data)
        else:
            data.warnings.append(f"{link_file.path.name}: нет столбца SYNTHETIC_F, физлиц нет")
        if {"PAYER", "BENEFICIARY"} <= set(link_file.columns):
            read_payments(link_file, data)
    if payments is not None:
        payment_file = open_csv(payments)
        data.files.append(payment_file.path.name)
        note_missing_columns(payment_file, PAYMENT_FIELDS, data)
        read_payments(payment_file, data)
    if not data.payments:
        data.warnings.append("Платежей нет: контрагенты и граф связей по платежам не построить")
    return data


def as_rows(records: dict[Any, Record], columns: list[str]) -> list[tuple[object, ...]]:
    return [tuple(record[column] for column in columns) for record in records.values()]


async def store(connection: Any, data: CaseData) -> dict[str, object]:
    """Полная перезаливка в одной транзакции: таблицы либо обновлены целиком, либо не тронуты."""
    report = data.report()
    company_columns = [*COMPANY_FIELDS, "source_line"]
    async with connection.transaction():
        await connection.execute(SCHEMA)
        await connection.execute("TRUNCATE companies, persons, payments")
        for table, records, columns in (
            ("companies", data.companies, company_columns),
            ("persons", data.persons, list(PERSON_FIELDS)),
            ("payments", data.payments, list(PAYMENT_FIELDS)),
        ):
            if records:
                await connection.copy_records_to_table(
                    table, records=as_rows(records, columns), columns=columns
                )
        await connection.execute(
            "INSERT INTO data_loads (report) VALUES ($1::jsonb)",
            json.dumps(report, ensure_ascii=False, default=str),
        )
    return report
