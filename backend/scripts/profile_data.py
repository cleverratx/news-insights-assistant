from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import pandas as pd
from case_data import (
    COMPANY_COLUMNS,
    LINK_PAYMENT_COLUMNS,
    LINK_PERSON_COLUMNS,
    PRODUCT_COLUMNS,
    PRODUCT_TITLES,
    CaseTables,
    LoadedTable,
    format_amount,
    is_positive_mark,
    load_case,
    share,
    to_number,
)

MAX_TOP_VALUES = 10
LOW_FILL_THRESHOLD = 0.5
LARGE_BUSINESS_REVENUE = 15_000_000_000


def column_overview(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    total = len(frame)
    for column in frame.columns:
        series = frame[column]
        filled = int(series.notna().sum())
        unique = int(series.nunique(dropna=True))
        sample = series.dropna().astype(str).unique()[:3]
        rows.append(
            {
                "Поле": column,
                "Заполнено": share(filled, total),
                "Уникальных": unique,
                "Примеры": ", ".join(sample) if len(sample) else "—",
            }
        )
    return pd.DataFrame(rows)


def markdown_table(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "Нет данных\n"
    header = "| " + " | ".join(str(column) for column in frame.columns) + " |"
    divider = "| " + " | ".join("---" for _ in frame.columns) + " |"
    lines = [header, divider]
    for row in frame.itertuples(index=False):
        cells = [str(value) if not pd.isna(value) else "—" for value in row]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def top_values(series: pd.Series, limit: int = MAX_TOP_VALUES) -> pd.DataFrame:
    counts = series.dropna().astype(str).value_counts().head(limit)
    total = int(series.notna().sum())
    return pd.DataFrame(
        {
            "Значение": counts.index,
            "Строк": counts.to_numpy(),
            "Доля": [f"{value / total:.1%}" if total else "—" for value in counts.to_numpy()],
        }
    )


def numeric_summary(series: pd.Series) -> pd.DataFrame:
    values = to_number(series).dropna()
    if values.empty:
        return pd.DataFrame()
    quantiles = values.quantile([0.25, 0.5, 0.75, 0.9, 0.99])
    rows = [
        ("минимум", format_amount(float(values.min()))),
        ("25-й процентиль", format_amount(float(quantiles.loc[0.25]))),
        ("медиана", format_amount(float(quantiles.loc[0.5]))),
        ("75-й процентиль", format_amount(float(quantiles.loc[0.75]))),
        ("90-й процентиль", format_amount(float(quantiles.loc[0.9]))),
        ("99-й процентиль", format_amount(float(quantiles.loc[0.99]))),
        ("максимум", format_amount(float(values.max()))),
        ("нулевых", str(int((values == 0).sum()))),
        ("отрицательных", str(int((values < 0).sum()))),
    ]
    return pd.DataFrame(rows, columns=["Показатель", "Значение"])


def missing_columns(frame: pd.DataFrame, expected: list[str]) -> list[str]:
    return [column for column in expected if column not in frame.columns]


def describe_file(table: LoadedTable) -> str:
    size_mb = table.path.stat().st_size / 1024 / 1024
    return (
        f"- `{table.path.name}`: {len(table.frame)} строк, {len(table.frame.columns)} столбцов, "
        f"{size_mb:.1f} МБ, кодировка {table.encoding}, разделитель `{table.separator}`\n"
    )


def payments_section(payments: pd.DataFrame, companies: pd.DataFrame, self_payments: int) -> str:
    if payments.empty:
        return (
            "Платежей не найдено: ни в файле связей, ни в отдельном файле "
            "df_Uid_payment_hack.csv рядом с ним. Передайте путь через --payments.\n"
        )

    amounts = (
        to_number(payments["PAY_AMT"]) if "PAY_AMT" in payments.columns else pd.Series(dtype=float)
    )
    counts = (
        to_number(payments["PAY_CNT"]) if "PAY_CNT" in payments.columns else pd.Series(dtype=float)
    )

    partners = pd.concat(
        [
            payments[["BENEFICIARY", "PAYER"]].rename(
                columns={"BENEFICIARY": "company", "PAYER": "partner"}
            ),
            payments[["PAYER", "BENEFICIARY"]].rename(
                columns={"PAYER": "company", "BENEFICIARY": "partner"}
            ),
        ],
        ignore_index=True,
    )
    per_company = partners.groupby("company")["partner"].nunique()

    company_ids = set(companies["SYNTHETIC_U"].astype(str)) if "SYNTHETIC_U" in companies else set()
    known = per_company.index.astype(str).isin(company_ids).sum() if company_ids else 0

    lines = [
        f"- строк платежей: {len(payments)}",
        f"- отброшено платежей самому себе (BENEFICIARY = PAYER): {self_payments}",
        f"- компаний с платежами: {len(per_company)}"
        + (f", из них есть в выгрузке юрлиц: {known}" if company_ids else ""),
        f"- контрагентов на компанию: медиана {per_company.median():.0f}, "
        f"90-й процентиль {per_company.quantile(0.9):.0f}, максимум {per_company.max():.0f}",
        f"- компаний с 5+ контрагентами: {int((per_company >= 5).sum())}",
    ]
    if not amounts.empty:
        lines.append(
            f"- объём платежа: медиана {format_amount(float(amounts.median()))}, "
            f"максимум {format_amount(float(amounts.max()))}"
        )
    if not counts.empty:
        lines.append(
            f"- количество платежей: медиана {counts.median():.0f}, максимум {counts.max():.0f}"
        )

    return "\n".join(lines) + "\n"


def consistency_section(
    companies: pd.DataFrame, persons: pd.DataFrame, payments: pd.DataFrame
) -> str:
    lines = []
    if "SYNTHETIC_U" in companies.columns:
        ids = companies["SYNTHETIC_U"].astype(str)
        duplicates = int(ids.duplicated().sum())
        lines.append(f"- дубликатов SYNTHETIC_U в выгрузке юрлиц: {duplicates}")
        known = set(ids)

        if not payments.empty:
            referenced = set(payments["BENEFICIARY"].astype(str)) | set(
                payments["PAYER"].astype(str)
            )
            unknown = referenced - known
            lines.append(
                f"- идентификаторов в платежах нет в выгрузке юрлиц: {len(unknown)} "
                f"из {len(referenced)}"
            )
        if not persons.empty and "SYNTHETIC_U" in persons.columns:
            linked = set(persons["SYNTHETIC_U"].astype(str))
            lines.append(
                f"- компаний со связанными физлицами: {len(linked & known)}; "
                f"идентификаторов физлиц без компании в выгрузке: {len(linked - known)}"
            )
    if "GROUP_ID" in companies.columns:
        groups = companies["GROUP_ID"].dropna()
        sizes = groups.value_counts()
        in_groups = int(sizes[sizes > 1].sum())
        largest = int(sizes.max()) if not sizes.empty else 0
        lines.append(
            f"- компаний в группах (GROUP_ID встречается больше одного раза): {in_groups}, "
            f"групп: {int((sizes > 1).sum())}, самая большая: {largest}"
        )
    return "\n".join(lines) + "\n" if lines else "Проверки не выполнялись.\n"


def usability_section(frame: pd.DataFrame) -> str:
    total = len(frame)
    constants = []
    sparse = []
    for column in frame.columns:
        series = frame[column]
        filled_rows = int(series.notna().sum())
        filled = filled_rows / total if total else 0
        if series.nunique(dropna=True) <= 1:
            constants.append(column)
        elif filled < LOW_FILL_THRESHOLD:
            sparse.append(f"{column} ({share(filled_rows, total)}, строк: {filled_rows})")

    lines = []
    lines.append(
        "- константы, для признаков бесполезны: " + (", ".join(constants) if constants else "нет")
    )
    lines.append(
        "- заполнено меньше половины строк, использовать осторожно: "
        + (", ".join(sparse) if sparse else "нет")
    )
    return "\n".join(lines) + "\n"


def build_report(case: CaseTables) -> str:
    companies_table = case.companies
    link_table = case.links
    companies = companies_table.frame
    persons = case.persons
    payments = case.payments

    parts: list[str] = []
    parts.append("# Профиль данных кейса\n")
    parts.append(
        f"Отчёт собран скриптом `backend/scripts/profile_data.py` {date.today():%d.%m.%Y}. "
        "Пересобирается одной командой, руками не правится.\n"
    )

    parts.append("\n## Файлы\n")
    parts.append(describe_file(companies_table))
    parts.append(describe_file(link_table))
    if case.payments_file is not None:
        parts.append(describe_file(case.payments_file))

    missing_company = missing_columns(companies, COMPANY_COLUMNS)
    payment_columns = set(link_table.frame.columns)
    if case.payments_file is not None:
        payment_columns |= set(case.payments_file.frame.columns)
    missing_link = [
        column for column in LINK_PERSON_COLUMNS if column not in link_table.frame.columns
    ] + [column for column in LINK_PAYMENT_COLUMNS if column not in payment_columns]
    parts.append("\n## Сверка с описанием кейса\n")
    parts.append(
        "- полей юрлиц из приложения 1 нет в файле: "
        + (", ".join(missing_company) if missing_company else "все на месте")
        + "\n"
    )
    parts.append(
        "- полей связей и платежей нет в файле: "
        + (", ".join(missing_link) if missing_link else "все на месте")
        + "\n"
    )
    extra = [column for column in companies.columns if column not in COMPANY_COLUMNS]
    parts.append("- поля сверх описания: " + (", ".join(extra) if extra else "нет") + "\n")

    parts.append("\n## Юридические лица: поля\n")
    parts.append(markdown_table(column_overview(companies)))

    if "REVENUE_COMPANY" in companies.columns:
        parts.append("\n## Выручка\n")
        parts.append(markdown_table(numeric_summary(companies["REVENUE_COMPANY"])))
        revenue = to_number(companies["REVENUE_COMPANY"])
        large = int((revenue >= LARGE_BUSINESS_REVENUE).sum())
        parts.append(
            f"\n- компаний с выручкой от 15 млрд (целевой сегмент кейса): {large} "
            f"({share(large, len(companies))})\n"
        )

    for column, title in (
        ("MAINOKVED_NAME", "Отрасли"),
        ("REGION", "Регионы"),
        ("ORG_TYPE", "Типы организаций"),
        ("ECO_MARK_LABEL", "Стадия развития"),
        ("CL_TYPE", "Тип клиента"),
        ("COMPANY_AGE_YEARS_GRADATION", "Возраст компании"),
    ):
        if column in companies.columns:
            parts.append(f"\n## {title}\n")
            parts.append(markdown_table(top_values(companies[column])))

    product_rows = []
    for column in PRODUCT_COLUMNS:
        if column not in companies.columns:
            continue
        ratio = companies[column].apply(is_positive_mark).mean()
        product_rows.append(
            {
                "Продукт": PRODUCT_TITLES.get(column, column),
                "Поле": column,
                "Доля компаний": f"{ratio:.1%}",
            }
        )
    if product_rows:
        parts.append("\n## Продукты банка\n")
        parts.append(markdown_table(pd.DataFrame(product_rows)))

    if "FTS_FLAG" in companies.columns:
        ratio = companies["FTS_FLAG"].apply(is_positive_mark).mean()
        parts.append("\n## Внешнеэкономическая деятельность\n")
        parts.append(f"- компаний с признаком ФТС: {ratio:.1%}\n")
        if "FTS_COUNTRY_NAME" in companies.columns:
            parts.append(markdown_table(top_values(companies["FTS_COUNTRY_NAME"])))

    parts.append("\n## Физические лица\n")
    if persons.empty:
        parts.append("Связанных физических лиц в файле не найдено.\n")
    else:
        parts.append(f"- строк: {len(persons)}\n")
        parts.append(markdown_table(column_overview(persons)))

    parts.append("\n## Платежи и контрагенты\n")
    parts.append(payments_section(payments, companies, case.self_payments))

    parts.append("\n## Консистентность\n")
    parts.append(consistency_section(companies, persons, payments))

    parts.append("\n## Что пригодно для признаков\n")
    parts.append(usability_section(companies))

    return "".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser(description="Первичный разбор выгрузок кейса (задача A1-08)")
    parser.add_argument("companies", type=Path, help="путь к df_Uid_data_hack.csv")
    parser.add_argument("links", type=Path, help="путь к df_Fid_Uid_link_hack.csv")
    parser.add_argument(
        "--payments",
        type=Path,
        help="путь к df_Uid_payment_hack.csv; по умолчанию ищется рядом с файлом связей",
    )
    parser.add_argument(
        "--out", type=Path, default=Path("docs/data-profile.md"), help="куда положить отчёт"
    )
    args = parser.parse_args()

    report = build_report(load_case(args.companies, args.links, args.payments))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report, encoding="utf-8")
    print(f"Отчёт: {args.out}")


if __name__ == "__main__":
    main()
