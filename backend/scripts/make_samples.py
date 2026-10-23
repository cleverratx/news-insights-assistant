from __future__ import annotations

import argparse
import random
from dataclasses import dataclass, field
from pathlib import Path

from case_data import COMPANY_COLUMNS, LINK_PAYMENT_COLUMNS, LINK_PERSON_COLUMNS

SEED = 20261021
OUT = Path(__file__).resolve().parents[2] / "data" / "samples"
LINK_COLUMNS = [*LINK_PERSON_COLUMNS, *LINK_PAYMENT_COLUMNS]
PRODUCT_FLAGS = {
    "rko": "CL_PRODUCT_RKO_MARK",
    "gar": "CL_PRODUCT_GAR_MARK",
    "loa": "CL_PRODUCT_LOA_MARK",
    "lea": "CL_PRODUCT_LEA_MARK",
    "dep": "CL_PRODUCT_DEP_MARK",
    "zpp": "CL_PRODUCT_ZPP_MARK",
    "fac": "FACTORING_MARK",
}
OKVED_NAMES = {
    "46.38": "Торговля оптовая прочими пищевыми продуктами, включая рыбу, ракообразных и моллюсков",
    "32.50": "Производство медицинских инструментов и оборудования",
    "62.01": "Разработка компьютерного программного обеспечения",
    "52.10": "Деятельность по складированию и хранению",
    "49.41": "Деятельность автомобильного грузового транспорта",
    "47.11": (
        "Торговля розничная преимущественно пищевыми продуктами в неспециализированных магазинах"
    ),
    "56.10": "Деятельность ресторанов и услуги по доставке продуктов питания",
    "64.91": "Деятельность по финансовой аренде (лизингу/сублизингу)",
    "10.20": "Переработка и консервирование рыбы, ракообразных и моллюсков",
    "68.20": "Аренда и управление собственным или арендованным недвижимым имуществом",
    "86.10": "Деятельность больничных организаций",
    "46.46": "Торговля оптовая фармацевтической продукцией",
    "24.10": "Производство чугуна, стали и ферросплавов",
    "64.19": "Денежное посредничество прочее",
    "26.20": "Производство компьютеров и периферийного оборудования",
    "41.20": "Строительство жилых и нежилых зданий",
}
REGIONS = [
    ("г. Москва", "Москва"),
    ("г. Санкт-Петербург", "Санкт-Петербург"),
    ("г. Екатеринбург", "Свердловская область"),
    ("г. Новосибирск", "Новосибирская область"),
    ("г. Казань", "Республика Татарстан"),
    ("г. Челябинск", "Челябинская область"),
]


STATE = "СОБСТВЕННОСТЬ СУБЪЕКТОВ РОССИЙСКОЙ ФЕДЕРАЦИИ"


@dataclass
class Company:
    id: str
    okved: str
    revenue: str
    city: str = "г. Москва"
    region: str = "Москва"
    products: tuple[str, ...] = ("rko",)
    countries: str = ""
    group: str = ""
    client: str = "CLIENT"
    ownership: str = "ЧАСТНАЯ СОБСТВЕННОСТЬ"
    age: str = "8-15"
    stage: str = "Оптимизация"
    staff: str = "16-100"
    overrides: dict[str, str] = field(default_factory=dict)

    def row(self) -> dict[str, str]:
        values = {
            "SYNTHETIC_U": self.id,
            "ORG_TYPE": "КОММ_ОРГ",
            "OKFS_NAME": self.ownership,
            "COMPANY_AGE_YEARS_GRADATION": self.age,
            "ECO_MARK_LABEL": self.stage,
            "MAINOKVED_CODE": self.okved,
            "MAINOKVED_NAME": OKVED_NAMES.get(self.okved, ""),
            "NEGATIVEMARK": "0.0",
            "AMOUNT_WORKERS_FNS_GRADATION": self.staff,
            "AMOUNT_WORKERS_SPARK_GRADATION": "",
            "CL_TYPE": self.client,
            "FTS_FLAG": "1.0" if self.countries else "0.0",
            "FTS_COUNTRY_NAME": self.countries,
            "REVENUE_COMPANY": self.revenue,
            "GROUP_ID": self.group or f"group_{self.id.lower()}_sb",
            "CITY": self.city,
            "REGION": self.region,
        }
        for key, column in PRODUCT_FLAGS.items():
            values[column] = "1.0" if key in self.products else "0.0"
        values.update(self.overrides)
        return values


def demo_companies() -> list[Company]:
    return [
        Company(
            "U_900001",
            "46.38",
            "18200000000.0",
            products=("rko", "loa", "lea", "zpp"),
            countries="КИТАЙ, ТУРЦИЯ",
            group="group_demo_food_sb",
        ),
        Company(
            "U_900002",
            "32.50",
            "3100000000.0",
            city="г. Екатеринбург",
            region="Свердловская область",
            products=("rko", "gar", "dep", "zpp"),
            countries="ОАЭ, КИТАЙ",
            ownership="СОВМЕСТНАЯ ЧАСТНАЯ И ИНОСТРАННАЯ СОБСТВЕННОСТЬ",
            age="16-25",
            stage="Бизнес в новой реальности",
        ),
        Company(
            "U_900003",
            "62.01",
            "1300000000.0",
            city="г. Магнитогорск",
            region="Челябинская область",
            products=("rko", "gar", "lea", "zpp"),
            countries="УЗБЕКИСТАН",
            staff="101-250",
            age="16-25",
        ),
    ]


def environment() -> list[Company]:
    """Окружение демо-компаний: группа, контрагенты по платежам, связанная через физлицо."""
    group = "group_demo_food_sb"
    return [
        Company("U_900101", "52.10", "850000000.0", group=group),
        Company("U_900102", "49.41", "640000000.0", group=group, products=("rko", "lea")),
        Company(
            "U_900103",
            "47.11",
            "2300000000.0",
            group=group,
            city="г. Казань",
            region="Республика Татарстан",
        ),
        Company("U_900201", "56.10", "4100000000.0"),
        Company(
            "U_900202", "56.10", "900000000.0", city="г. Санкт-Петербург", region="Санкт-Петербург"
        ),
        Company("U_900203", "47.11", "12000000000.0"),
        Company("U_900204", "64.91", "25000000000.0"),
        Company(
            "U_900205",
            "10.20",
            "1500000000.0",
            city="г. Новосибирск",
            region="Новосибирская область",
        ),
        Company(
            "U_900206",
            "86.10",
            "5200000000.0",
            city="г. Екатеринбург",
            region="Свердловская область",
            ownership=STATE,
        ),
        Company("U_900207", "46.46", "7700000000.0"),
        Company(
            "U_900208",
            "24.10",
            "96000000000.0",
            city="г. Магнитогорск",
            region="Челябинская область",
            staff="Более 250",
        ),
        Company("U_900209", "64.19", "41000000000.0"),
        Company("U_900210", "26.20", "380000000.0"),
        Company("U_900301", "68.20", "120000000.0"),
    ]


def peers(rng: random.Random) -> list[Company]:
    """По 12 компаний на каждый ОКВЭД демо-компаний: для места по выручке в отрасли."""
    companies = []
    number = 900500
    for okved in ("46.38", "32.50", "62.01"):
        for _ in range(12):
            number += 1
            city, region = rng.choice(REGIONS)
            products = tuple(
                key for key in ("rko", "gar", "loa", "lea", "dep", "zpp") if rng.random() < 0.3
            )
            companies.append(
                Company(
                    f"U_{number}",
                    okved,
                    f"{rng.randint(50, 20000) * 1_000_000}.0",
                    city=city,
                    region=region,
                    products=products or ("rko",),
                    countries=rng.choice(["", "", "КИТАЙ", "ТУРЦИЯ", "КАЗАХСТАН"]),
                    client=rng.choice(["CLIENT", "CLIENT", "PROSPECT", "CLOSE_CLIENT"]),
                    age=rng.choice(["0-3", "4-7", "8-15", "16-25"]),
                    stage=rng.choice(["Оптимизация", "Бизнес в новой реальности", "Рождение"]),
                )
            )
    return companies


# Строки с мусором: каждая проверяет одно правило очистки загрузчика (docs/data.md).
NOISY = [
    (
        "U_900401",
        "пустые поля",
        {
            "OKFS_NAME": "",
            "ECO_MARK_LABEL": "nan",
            "REGION": "",
            "CITY": "—",
            "AMOUNT_WORKERS_FNS_GRADATION": "",
            "CL_TYPE": "",
            "REVENUE_COMPANY": "",
        },
    ),
    ("U_900402", "нераспознанный флаг", {"CL_PRODUCT_LOA_MARK": "возможно", "FTS_FLAG": "н/д"}),
    ("U_900403", "отрицательная выручка", {"REVENUE_COMPANY": "-5000000.0"}),
    ("U_900404", "нулевая выручка", {"REVENUE_COMPANY": "0"}),
    ("U_900405", "выручка с пробелами и запятой", {"REVENUE_COMPANY": "1 234 567 890,50"}),
    (
        "U_900406",
        "несуществующий класс ОКВЭД, отрасль по названию",
        {"MAINOKVED_CODE": "34.10", "MAINOKVED_NAME": "Деятельность ресторанов и кафе"},
    ),
    (
        "U_900407",
        "пустой код ОКВЭД, отрасль по названию",
        {"MAINOKVED_CODE": "", "MAINOKVED_NAME": "Торговля оптовая неспециализированная"},
    ),
    ("U_900408", "битый код и пустое название", {"MAINOKVED_CODE": "abc", "MAINOKVED_NAME": ""}),
    ("U_900409", "ОКВЭД с запятой", {"MAINOKVED_CODE": "41,20", "MAINOKVED_NAME": ""}),
    ("U_900410", "тип клиента вне справочника", {"CL_TYPE": "VIP"}),
    ("U_900411", "страны ФТС при FTS_FLAG = 0", {"FTS_FLAG": "0.0", "FTS_COUNTRY_NAME": "ТУРЦИЯ"}),
    (
        "U_900412",
        "недружественная страна и хаб платежей",
        {"FTS_FLAG": "1", "FTS_COUNTRY_NAME": "ЛИТВА, ГОНКОНГ"},
    ),
    (
        "U_900413",
        "потенциальный клиент без продуктов",
        {"CL_TYPE": "PROSPECT", "CL_PRODUCT_RKO_MARK": "0.0"},
    ),
    (
        "U_900414",
        "крупнейшая выручка при штате менее 15",
        {"REVENUE_COMPANY": "30000000000.0", "AMOUNT_WORKERS_FNS_GRADATION": "Менее 15"},
    ),
    ("U_900415", "ФНС и СПАРК расходятся", {"AMOUNT_WORKERS_SPARK_GRADATION": "101-250"}),
    (
        "U_900416",
        "неразрывные пробелы и кавычки",
        {"CITY": " г. Омск ", "REGION": '"Омская область"'},
    ),
    ("U_900417", "флаги словами", {"CL_PRODUCT_LOA_MARK": "да", "CL_PRODUCT_DEP_MARK": "Нет"}),
    ("U_900420", "заглушка 1000 ₽ вместо выручки", {"REVENUE_COMPANY": "1000.0"}),
]
DUPLICATE = ("U_900001", "дубликат демо-компании с пустыми полями")
SHORT_ROW = "U_900418;КОММ_ОРГ;ЧАСТНАЯ СОБСТВЕННОСТЬ"
LONG_ROW_ID = "U_900419"


def companies_csv(rng: random.Random) -> list[str]:
    rows = [";".join(COMPANY_COLUMNS)]
    base = Company("", "41.20", "500000000.0")
    for company in [*demo_companies(), *environment(), *peers(rng)]:
        rows.append(";".join(company.row()[column] for column in COMPANY_COLUMNS))
    for company_id, _, overrides in NOISY:
        noisy = Company(company_id, base.okved, base.revenue, overrides=overrides)
        rows.append(";".join(noisy.row()[column] for column in COMPANY_COLUMNS))
    duplicate = Company(
        DUPLICATE[0], "46.38", "", products=(), overrides={"REGION": "", "CITY": ""}
    )
    rows.append(";".join(duplicate.row()[column] for column in COMPANY_COLUMNS))
    rows.append(SHORT_ROW)
    long_row = Company(LONG_ROW_ID, base.okved, base.revenue).row()
    rows.append(";".join(long_row[column] for column in COMPANY_COLUMNS) + ";лишнее;поле")
    return rows


PERSONS = [
    ("F_900001", "Москва", "МОСКОВСКИЙ", "U_900001"),
    ("F_900001", "Москва", "МОСКОВСКИЙ", "U_900301"),
    ("F_900002", "Казань", "ПОВОЛЖСКИЙ", "U_900001"),
    ("F_900003", "Екатеринбург", "УРАЛЬСКИЙ", "U_900002"),
    ("F_900004", "Челябинск", "УРАЛЬСКИЙ", "U_900003"),
    ("F_900004", "Челябинск", "УРАЛЬСКИЙ", "U_900003"),
    ("F_900005", "", "", ""),
]
# Получатель, плательщик, сумма, число платежей.
PAYMENTS = [
    ("U_900001", "U_900201", "5500000000.0", "420"),
    ("U_900001", "U_900202", "1500000000.0", "160"),
    ("U_900001", "U_900203", "2000000000.0", "95"),
    ("U_900001", "U_900103", "1000000000.0", "60"),
    ("U_900205", "U_900001", "4800000000.0", "52"),
    ("U_900102", "U_900001", "4000000000.0", "310"),
    ("U_900204", "U_900001", "1200000000.0", "12"),
    ("U_900002", "U_900206", "2100000000.0", "24"),
    ("U_900002", "U_900207", "900000000.0", "18"),
    ("U_900210", "U_900002", "400000000.0", "30"),
    ("U_900003", "U_900208", "585000000.0", "14"),
    ("U_900003", "U_900209", "325000000.0", "9"),
    ("U_900003", "U_900201", "390000000.0", "11"),
    ("U_900204", "U_900003", "60000000.0", "12"),
    ("U_900001", "U_900201", "0", "1"),
    ("U_900001", "U_900001", "100.0", "1"),
    ("U_900001", "U_900999", "", "3"),
]


def links_csv() -> list[str]:
    rows = [";".join(LINK_COLUMNS)]
    rows += [";".join([*person, "", "", "", ""]) for person in PERSONS]
    rows += [";".join(["", "", "", "", *payment]) for payment in PAYMENTS]
    return rows


def render() -> dict[str, str]:
    rng = random.Random(SEED)  # noqa: S311 — воспроизводимые тестовые данные, не криптография
    return {
        "df_Uid_data_hack.csv": "\n".join(companies_csv(rng)) + "\n",
        "df_Fid_Uid_link_hack.csv": "\n".join(links_csv()) + "\n",
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Синтетические выгрузки для сквозного сценария (A2-08)"
    )
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    for name, content in render().items():
        (args.out / name).write_text(content, encoding="utf-8-sig", newline="\n")
        print(f"{args.out / name}: {content.count(chr(10)) - 1} строк")


if __name__ == "__main__":
    main()
