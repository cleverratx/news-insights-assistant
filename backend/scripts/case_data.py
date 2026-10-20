from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

COMPANY_COLUMNS = [
    "SYNTHETIC_U",
    "ORG_TYPE",
    "OKFS_NAME",
    "COMPANY_AGE_YEARS_GRADATION",
    "ECO_MARK_LABEL",
    "MAINOKVED_CODE",
    "MAINOKVED_NAME",
    "NEGATIVEMARK",
    "AMOUNT_WORKERS_FNS_GRADATION",
    "AMOUNT_WORKERS_SPARK_GRADATION",
    "CL_TYPE",
    "CL_PRODUCT_RKO_MARK",
    "CL_PRODUCT_GAR_MARK",
    "CL_PRODUCT_LOA_MARK",
    "CL_PRODUCT_LEA_MARK",
    "CL_PRODUCT_DEP_MARK",
    "CL_PRODUCT_ZPP_MARK",
    "FTS_FLAG",
    "FTS_COUNTRY_NAME",
    "FACTORING_MARK",
    "REVENUE_COMPANY",
    "GROUP_ID",
    "CITY",
    "REGION",
]

LINK_PERSON_COLUMNS = ["SYNTHETIC_F", "CITY_NAME", "RC_NAME", "SYNTHETIC_U"]
LINK_PAYMENT_COLUMNS = ["BENEFICIARY", "PAYER", "PAY_AMT", "PAY_CNT"]

PRODUCT_COLUMNS = [
    "CL_PRODUCT_RKO_MARK",
    "CL_PRODUCT_GAR_MARK",
    "CL_PRODUCT_LOA_MARK",
    "CL_PRODUCT_LEA_MARK",
    "CL_PRODUCT_DEP_MARK",
    "CL_PRODUCT_ZPP_MARK",
    "FACTORING_MARK",
]

PRODUCT_TITLES = {
    "CL_PRODUCT_RKO_MARK": "расчётно-кассовое обслуживание",
    "CL_PRODUCT_GAR_MARK": "банковские гарантии",
    "CL_PRODUCT_LOA_MARK": "кредитные продукты",
    "CL_PRODUCT_LEA_MARK": "лизинг",
    "CL_PRODUCT_DEP_MARK": "депозиты",
    "CL_PRODUCT_ZPP_MARK": "зарплатный проект",
    "FACTORING_MARK": "факторинг",
}

ENCODINGS = ["utf-8-sig", "utf-8", "cp1251"]

TEXT_COLUMNS = {
    "SYNTHETIC_U": "string",
    "SYNTHETIC_F": "string",
    "GROUP_ID": "string",
    "MAINOKVED_CODE": "string",
    "BENEFICIARY": "string",
    "PAYER": "string",
}


@dataclass
class LoadedTable:
    frame: pd.DataFrame
    path: Path
    encoding: str
    separator: str


def sniff_separator(path: Path, encoding: str) -> str:
    with path.open("r", encoding=encoding, errors="replace") as handle:
        sample = handle.read(16384)
    try:
        return str(csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter)
    except csv.Error:
        counts = {sep: sample.count(sep) for sep in [",", ";", "\t", "|"]}
        return max(counts, key=lambda key: counts[key])


def load_table(path: Path) -> LoadedTable:
    last_error: Exception | None = None
    for encoding in ENCODINGS:
        try:
            separator = sniff_separator(path, encoding)
            frame = pd.read_csv(
                path,
                sep=separator,
                encoding=encoding,
                low_memory=False,
                dtype=TEXT_COLUMNS,
            )
        except (UnicodeDecodeError, pd.errors.ParserError) as error:
            last_error = error
            continue
        frame.columns = [str(column).strip() for column in frame.columns]
        return LoadedTable(frame=frame, path=path, encoding=encoding, separator=separator)
    raise SystemExit(f"Не удалось прочитать {path}: {last_error}")


def split_link_table(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    person_columns = [column for column in LINK_PERSON_COLUMNS if column in frame.columns]
    payment_columns = [column for column in LINK_PAYMENT_COLUMNS if column in frame.columns]

    persons = pd.DataFrame(columns=person_columns)
    payments = pd.DataFrame(columns=payment_columns)

    if "SYNTHETIC_F" in frame.columns:
        persons = frame[person_columns].dropna(subset=["SYNTHETIC_F"]).drop_duplicates()
    if {"BENEFICIARY", "PAYER"}.issubset(frame.columns):
        payments = frame[payment_columns].dropna(subset=["BENEFICIARY", "PAYER"])

    return persons, payments


def to_number(series: pd.Series) -> pd.Series:
    if series.dtype.kind in "if":
        return series
    cleaned = (
        series.astype(str)
        .str.replace(" ", "", regex=False)
        .str.replace(" ", "", regex=False)
        .str.replace(",", ".", regex=False)
    )
    return pd.to_numeric(cleaned, errors="coerce")


def is_positive_mark(value: object) -> bool:
    if value is None:
        return False
    text = str(value).strip().lower()
    return text not in {"", "nan", "none", "0", "0.0", "нет", "no", "false", "n"}


def format_amount(value: float) -> str:
    if pd.isna(value):
        return "—"
    for limit, suffix, divider in (
        (1e12, " трлн", 1e12),
        (1e9, " млрд", 1e9),
        (1e6, " млн", 1e6),
        (1e3, " тыс", 1e3),
    ):
        if abs(value) >= limit:
            return f"{value / divider:,.1f}{suffix}".replace(",", " ")
    return f"{value:,.0f}".replace(",", " ")


def counterparties(payments: pd.DataFrame, company_id: object) -> pd.DataFrame:
    if payments.empty:
        return pd.DataFrame(columns=["partner", "role", "PAY_AMT", "PAY_CNT"])

    incoming = payments[payments["BENEFICIARY"].astype(str) == str(company_id)].copy()
    incoming["partner"] = incoming["PAYER"].astype(str)
    incoming["role"] = "платит нам"

    outgoing = payments[payments["PAYER"].astype(str) == str(company_id)].copy()
    outgoing["partner"] = outgoing["BENEFICIARY"].astype(str)
    outgoing["role"] = "платим мы"

    merged = pd.concat([incoming, outgoing], ignore_index=True)
    if merged.empty:
        return pd.DataFrame(columns=["partner", "role", "PAY_AMT", "PAY_CNT"])

    for column in ("PAY_AMT", "PAY_CNT"):
        if column in merged.columns:
            merged[column] = to_number(merged[column])
        else:
            merged[column] = pd.NA

    grouped = (
        merged.groupby(["partner", "role"], as_index=False)
        .agg({"PAY_AMT": "sum", "PAY_CNT": "sum"})
        .sort_values("PAY_AMT", ascending=False)
    )
    return grouped
