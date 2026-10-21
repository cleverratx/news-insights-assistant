from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import pandas as pd
from case_data import (
    PRODUCT_COLUMNS,
    PRODUCT_TITLES,
    counterparties,
    format_amount,
    is_positive_mark,
    load_case,
    to_number,
)

LARGE_BUSINESS_REVENUE = 15_000_000_000

RATE_SENSITIVE = [
    "CL_PRODUCT_LOA_MARK",
    "CL_PRODUCT_LEA_MARK",
    "CL_PRODUCT_GAR_MARK",
    "FACTORING_MARK",
]
KEY_FIELDS = ["MAINOKVED_NAME", "REGION", "REVENUE_COMPANY"]
TOP_PARTNERS = 5
MIN_PARTNERS = 5
DEBT_PRODUCTS = ["CL_PRODUCT_LOA_MARK", "CL_PRODUCT_LEA_MARK"]
MISSING_PARTNER = "нет в выгрузке юрлиц"

# Роли демо-компаний: ленты должны заметно отличаться, поэтому берём по одной компании
# каждого типа бизнеса. Тип — по классу ОКВЭД, первым двум цифрам кода.
ARCHETYPES: dict[str, tuple[str, set[str]]] = {
    "importer": ("импортёр", {"45", "46"}),
    "manufacturing": ("производство", {f"{number:02d}" for number in range(10, 34)}),
    "services_retail": (
        "услуги или ритейл",
        {"47", "49", "50", "51", "52", "53", "55", "56"}
        | {f"{number:02d}" for number in range(58, 64)}
        | {f"{number:02d}" for number in range(68, 83)}
        | {f"{number:02d}" for number in range(85, 97)},
    ),
}
TRANSPORT = {"49", "50", "51", "52", "53"}
LEGEND_ROLES = {"платит нам": "покупатель", "платим мы": "поставщик"}


def industry_section(code: object) -> str:
    text = str(code).strip()
    digits = "".join(character for character in text if character.isdigit())
    return digits[:2] if digits else "нет"


def archetype_of(section: str) -> str | None:
    return next((key for key, (_, sections) in ARCHETYPES.items() if section in sections), None)


def blockers(row: pd.Series, partner_count: int) -> list[str]:
    """Чего не хватает строке для демо: на бедной строке половина фич профиля не отработает."""
    missing = []
    if not is_positive_mark(row.get("FTS_FLAG")):
        missing.append("нет ВЭД (FTS_FLAG)")
    if not any(is_positive_mark(row.get(column)) for column in DEBT_PRODUCTS):
        missing.append("нет кредита или лизинга")
    group = row.get("GROUP_ID")
    if not isinstance(group, str) or not group.strip() or row.get("_group_size", 1) <= 1:
        missing.append("нет группы компаний (GROUP_ID)")
    if partner_count < MIN_PARTNERS:
        missing.append(f"контрагентов меньше {MIN_PARTNERS}: {partner_count}")
    return missing


def score_company(row: pd.Series, partner_count: int, person_count: int) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []

    if "FTS_FLAG" in row.index and is_positive_mark(row["FTS_FLAG"]):
        score += 2
        country = row.get("FTS_COUNTRY_NAME")
        suffix = f" ({country})" if isinstance(country, str) and country.strip() else ""
        reasons.append(
            f"есть внешнеэкономическая деятельность{suffix}: отзовётся на пошлины и курс"
        )

    rate_products = [
        PRODUCT_TITLES[column]
        for column in RATE_SENSITIVE
        if column in row.index and is_positive_mark(row[column])
    ]
    if rate_products:
        score += 2
        reasons.append("чувствительна к ставке: " + ", ".join(rate_products))

    if partner_count >= 5:
        score += 2
        reasons.append(f"контрагентов в платежах: {partner_count}")
    elif partner_count >= 2:
        score += 1
        reasons.append(f"контрагентов в платежах: {partner_count}")

    if row.get("_group_size", 0) > 1:
        score += 1
        reasons.append(f"входит в группу компаний из {int(row['_group_size'])} юрлиц")

    filled = sum(1 for field in KEY_FIELDS if field in row.index and pd.notna(row[field]))
    score += filled
    if filled < len(KEY_FIELDS):
        reasons.append("часть ключевых полей пустая — проверить вручную")

    revenue = to_number(pd.Series([row.get("REVENUE_COMPANY")])).iloc[0]
    if pd.notna(revenue) and revenue >= 15_000_000_000:
        score += 1
        reasons.append(
            f"выручка {format_amount(float(revenue))} — крупный бизнес из постановки кейса"
        )

    if person_count:
        score += 1
        reasons.append(f"связанных физлиц: {person_count}")

    if (
        "CL_TYPE" in row.index
        and isinstance(row["CL_TYPE"], str)
        and "действ" in row["CL_TYPE"].lower()
    ):
        score += 1
        reasons.append("действующий клиент банка")

    return score, reasons


def build_cards(
    companies: pd.DataFrame,
    persons: pd.DataFrame,
    payments: pd.DataFrame,
    count: int,
    min_revenue: float,
) -> tuple[list[dict[str, object]], bool]:
    frame = companies.drop_duplicates(subset=["SYNTHETIC_U"]).copy()

    if "GROUP_ID" in frame.columns:
        sizes = frame["GROUP_ID"].map(frame["GROUP_ID"].value_counts())
        frame["_group_size"] = sizes.fillna(1)
    else:
        frame["_group_size"] = 1

    partner_counts: dict[str, int] = {}
    if not payments.empty:
        payments = payments[payments["BENEFICIARY"].astype(str) != payments["PAYER"].astype(str)]
        pairs = pd.concat(
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
        partner_counts = pairs.groupby("company")["partner"].nunique().to_dict()
        partner_counts = {str(key): int(value) for key, value in partner_counts.items()}

    person_counts: dict[str, int] = {}
    if not persons.empty and "SYNTHETIC_U" in persons.columns:
        counts = persons.groupby("SYNTHETIC_U")["SYNTHETIC_F"].nunique().to_dict()
        person_counts = {str(key): int(value) for key, value in counts.items()}

    scored = []
    for _, row in frame.iterrows():
        company_id = str(row.get("SYNTHETIC_U"))
        partner_count = partner_counts.get(company_id, 0)
        person_count = person_counts.get(company_id, 0)
        score, reasons = score_company(row, partner_count, person_count)
        revenue = to_number(pd.Series([row.get("REVENUE_COMPANY")])).iloc[0]
        section = industry_section(row.get("MAINOKVED_CODE"))
        scored.append(
            {
                "id": company_id,
                "row": row,
                "score": score,
                "reasons": reasons,
                "partners": partner_count,
                "persons": person_count,
                "section": section,
                "archetype": archetype_of(section),
                "blockers": blockers(row, partner_count),
                "revenue": float(revenue) if pd.notna(revenue) else 0.0,
            }
        )

    large = [item for item in scored if float(str(item["revenue"])) >= min_revenue]
    segment_only = len(large) >= count
    if segment_only:
        scored = large

    picked = pick(scored, count)

    industries = dict(
        zip(
            frame["SYNTHETIC_U"].astype(str),
            frame.get("MAINOKVED_NAME", pd.Series(dtype=str)).astype(str),
            strict=False,
        )
    )
    sections = dict(
        zip(
            frame["SYNTHETIC_U"].astype(str),
            frame.get("MAINOKVED_CODE", pd.Series(dtype=str)).map(industry_section),
            strict=False,
        )
    )
    for item in picked:
        partners = counterparties(payments, item["id"]).head(TOP_PARTNERS).copy()
        partners["industry"] = partners["partner"].map(
            lambda partner: industries.get(str(partner), MISSING_PARTNER)
        )
        partners["section"] = partners["partner"].map(lambda partner: sections.get(str(partner)))
        item["top_partners"] = partners
    return picked, segment_only


def rank(item: dict[str, object]) -> tuple[int, int]:
    return int(str(item["score"])), int(str(item["partners"]))


def pick(scored: list[dict[str, object]], count: int) -> list[dict[str, object]]:
    """По одной лучшей компании на роль из ARCHETYPES среди тех, кому ничего не мешает.
    Если для роли таких нет, слот добирается лучшей подходящей компанией другой отрасли.
    Если и подходящих не осталось — берём лучшую из остальных, сначала недостающей роли;
    в карточке тогда перечислено, чего ей не хватает."""
    ordered = sorted(scored, key=rank, reverse=True)
    fit = [item for item in ordered if not item["blockers"]]
    picked: list[dict[str, object]] = []
    ids: set[str] = set()
    sections: set[str] = set()

    def free(item: dict[str, object]) -> bool:
        return str(item["id"]) not in ids and str(item["section"]) not in sections

    def fill(pool: list[dict[str, object]], by_role: bool) -> None:
        roles = [key for key in ARCHETYPES if all(item["archetype"] != key for item in picked)]
        for role in roles if by_role else [None]:
            for item in pool:
                if len(picked) >= count:
                    return
                if free(item) and (role is None or item["archetype"] == role):
                    picked.append(item)
                    ids.add(str(item["id"]))
                    sections.add(str(item["section"]))
                    if role is not None:
                        break

    fill(fit, by_role=True)
    fill(fit, by_role=False)
    fill(ordered, by_role=True)
    fill(ordered, by_role=False)
    return picked


def archetype_title(item: dict[str, object]) -> str:
    archetype = item["archetype"]
    return ARCHETYPES[str(archetype)][0] if archetype else "другая отрасль"


def render_card(item: dict[str, object]) -> str:
    row = item["row"]
    lines = [f"\n## Компания {item['id']}\n", f"\n**Роль в демо:** {archetype_title(item)}\n"]
    missing = item["blockers"]
    if isinstance(missing, list) and missing:
        lines.append(
            "\n**Не проходит отбор, взята за неимением лучшей:** " + "; ".join(missing) + "\n"
        )
    lines.append("\n")

    facts = [
        ("Отрасль", row.get("MAINOKVED_NAME")),
        ("Код ОКВЭД", row.get("MAINOKVED_CODE")),
        ("Тип организации", row.get("ORG_TYPE")),
        ("Форма собственности", row.get("OKFS_NAME")),
        ("Возраст", row.get("COMPANY_AGE_YEARS_GRADATION")),
        ("Стадия", row.get("ECO_MARK_LABEL")),
        ("Сотрудники (ФНС)", row.get("AMOUNT_WORKERS_FNS_GRADATION")),
        ("Город", row.get("CITY")),
        ("Регион", row.get("REGION")),
    ]
    revenue = to_number(pd.Series([row.get("REVENUE_COMPANY")])).iloc[0]
    facts.append(("Выручка", format_amount(float(revenue)) if pd.notna(revenue) else None))

    table = pd.DataFrame(
        [
            {"Признак": title, "Значение": value}
            for title, value in facts
            if value is not None and str(value) != "nan"
        ]
    )
    lines.append(markdown_table(table))

    products = [
        PRODUCT_TITLES[column]
        for column in PRODUCT_COLUMNS
        if column in row.index and is_positive_mark(row[column])
    ]
    lines.append("\n**Продукты банка:** " + (", ".join(products) if products else "нет") + "\n")

    if int(row.get("_group_size", 1)) > 1:
        lines.append(
            f"\n**Группа компаний:** {row.get('GROUP_ID')}, "
            f"юрлиц в группе: {int(row['_group_size'])}\n"
        )

    partners = item["top_partners"]
    lines.append(f"\n**Контрагенты (всего {item['partners']}, показаны крупнейшие):**\n")
    if isinstance(partners, pd.DataFrame) and not partners.empty:
        shown = partners.copy()
        shown["PAY_AMT"] = shown["PAY_AMT"].map(lambda value: format_amount(float(value)))
        shown["PAY_CNT"] = shown["PAY_CNT"].map(
            lambda value: "—" if pd.isna(value) else f"{int(value)}"
        )
        shown = shown.rename(
            columns={
                "partner": "Контрагент",
                "role": "Направление",
                "PAY_AMT": "Объём",
                "PAY_CNT": "Платежей",
                "industry": "Отрасль контрагента",
            }
        )
        lines.append(markdown_table(shown))
    else:
        lines.append("Платежей в выгрузке нет\n")

    lines.append("\n**Почему выбрана:**\n")
    for reason in item["reasons"]:
        lines.append(f"- {reason}\n")

    lines.append("\n**Что должно отзываться в ленте:** ")
    topics = ["события отрасли", f"регион {row.get('REGION')}"]
    if any(is_positive_mark(row.get(column)) for column in RATE_SENSITIVE if column in row.index):
        topics.append("ставка ЦБ и стоимость денег")
    if "FTS_FLAG" in row.index and is_positive_mark(row["FTS_FLAG"]):
        topics.append("пошлины, курс, внешняя торговля")
    if item["partners"]:
        topics.append("новости контрагентов")
    lines.append(", ".join(topics) + "\n")

    return "".join(lines)


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


def legend_role(direction: str, section: object) -> str:
    """Роль по умолчанию: кто платит нам — покупатель, кому платим — поставщик, а если
    это транспортная компания — перевозчик. Правится руками вместе с названием."""
    if direction == "платим мы" and section in TRANSPORT:
        return "перевозчик"
    return LEGEND_ROLES.get(direction, "")


def render_legend(items: list[dict[str, object]]) -> str:
    """Заготовка в формате data/samples/legends.yaml (ADR 0003): остаётся вписать названия,
    а для контрагентов, которых нет в выгрузке, — отрасль и код ОКВЭД."""
    lines = [
        "# Заготовка демо-легенды. Заполненная легенда — data/samples/legends.yaml, формат",
        "# и правила — в её шапке и в ADR 0003. Названия вымышленные: проверьте поиском,",
        "# что такой компании нет. Роли проставлены по направлению платежей, проверьте их.",
        "",
        "companies:",
    ]
    for item in items:
        row = item["row"]
        lines.append(
            f"  # {archetype_title(item)}: {row.get('MAINOKVED_NAME', '')}, "
            f"{row.get('REGION', '')}"
        )
        lines.append(f"  - synthetic_u: \"{item['id']}\"")
        lines.append('    name: ""')
        lines.append('    description: ""')
        lines.append("    brands: []")
        partners = item["top_partners"]
        if not isinstance(partners, pd.DataFrame) or partners.empty:
            lines.append("    partners: []")
            lines.append("")
            continue
        lines.append("    partners:")
        seen: set[str] = set()
        for partner in partners.itertuples(index=False):
            if partner.partner in seen:
                continue
            seen.add(partner.partner)
            lines.append(f'      - synthetic_u: "{partner.partner}"')
            lines.append('        name: ""')
            lines.append(f'        role: "{legend_role(partner.role, partner.section)}"')
            if partner.industry == MISSING_PARTNER:
                lines.append('        industry: ""  # нет в выгрузке юрлиц: впишите отрасль')
                lines.append('        okved_code: ""')
            else:
                lines.append(f"        # в выгрузке: {partner.industry}")
        lines.append("")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Выбор эталонных компаний для демо (задача A1-09)")
    parser.add_argument("companies", type=Path, help="путь к df_Uid_data_hack.csv")
    parser.add_argument("links", type=Path, help="путь к df_Fid_Uid_link_hack.csv")
    parser.add_argument(
        "--payments",
        type=Path,
        help="путь к df_Uid_payment_hack.csv; по умолчанию ищется рядом с файлом связей",
    )
    parser.add_argument("-n", "--count", type=int, default=3, help="сколько компаний отобрать")
    parser.add_argument(
        "--min-revenue",
        type=float,
        default=LARGE_BUSINESS_REVENUE,
        help="нижняя граница выручки; по умолчанию сегмент кейса — от 15 млрд",
    )
    parser.add_argument("--out", type=Path, default=Path("docs/demo-companies.md"))
    parser.add_argument("--legend", type=Path, default=Path("data/samples/legends.generated.yaml"))
    args = parser.parse_args()

    case = load_case(args.companies, args.links, args.payments)
    if case.payments.empty:
        print("Внимание: платежей нет, контрагенты не учтены. Проверьте --payments.")

    picked, segment_only = build_cards(
        case.companies.frame, case.persons, case.payments, args.count, args.min_revenue
    )

    segment_note = (
        f"Кандидаты ограничены целевым сегментом кейса: выручка от "
        f"{format_amount(args.min_revenue)}."
        if segment_only
        else f"Компаний с выручкой от {format_amount(args.min_revenue)} меньше, чем нужно, "
        "поэтому отбор шёл по всей выгрузке."
    )
    report = [
        "# Эталонные компании для демо\n",
        f"Отобраны скриптом `backend/scripts/pick_demo_companies.py` {date.today():%d.%m.%Y}. "
        f"Обязательные условия: ВЭД (`FTS_FLAG`), кредит или лизинг, группа компаний "
        f"(`GROUP_ID`) и от {MIN_PARTNERS} контрагентов в платежах — на бедной строке половина "
        "признаков профиля не отработает. Из прошедших берётся по одной компании на роль: "
        "импортёр, производство, услуги или ритейл; внутри роли — по баллу за продукты, "
        "контрагентов, связанных физлиц и заполненность ключевых полей. "
        f"{segment_note} Платежи самому себе не учитываются.\n",
    ]
    summary = pd.DataFrame(
        [
            {
                "Компания": item["id"],
                "Роль в демо": archetype_title(item),
                "Отрасль": item["row"].get("MAINOKVED_NAME"),
                "Регион": item["row"].get("REGION"),
                "Контрагентов": item["partners"],
                "Балл": item["score"],
            }
            for item in picked
        ]
    )
    report.append("\n## Отобранные\n")
    report.append(markdown_table(summary))
    for item in picked:
        report.append(render_card(item))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("".join(report), encoding="utf-8")

    args.legend.parent.mkdir(parents=True, exist_ok=True)
    args.legend.write_text(render_legend(picked), encoding="utf-8")

    print(f"Карточки: {args.out}")
    print(f"Заготовка легенды: {args.legend}")


if __name__ == "__main__":
    main()
