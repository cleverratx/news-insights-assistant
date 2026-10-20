"""Продуктовый профиль и внешнеэкономическая деятельность, а из них — чувствительность к темам
новостей: ставка, валюта, торговля, санкции, рынок труда.

Флаги продуктов говорят о продуктах в Альфа-Банке, а не вообще: отсутствие кредита у нас не
значит, что у компании нет долга в другом банке. Поэтому «нет продукта» — не признак.
"""

from dataclasses import dataclass, field

from app.contracts.common import BankProduct
from app.contracts.news import Topic
from app.contracts.profile import (
    Exposure,
    ExposureDirection,
    FeatureKind,
    ForeignTrade,
    ProfileFeature,
)
from app.profile.row import CompanyRow, source_field

PRODUCTS = (
    (BankProduct.LOAN, "has_loan", "Кредитные продукты в Альфа-Банке"),
    (BankProduct.LEASING, "has_leasing", "Лизинг в Альфа-Банке"),
    (BankProduct.GUARANTEE, "has_guarantee", "Банковские гарантии в Альфа-Банке"),
    (BankProduct.FACTORING, "has_factoring", "Факторинг в Альфа-Банке"),
    (BankProduct.DEPOSIT, "has_deposit", "Депозиты в Альфа-Банке"),
    (BankProduct.SETTLEMENT, "has_settlement", "Расчётный счёт в Альфа-Банке"),
    (BankProduct.PAYROLL, "has_payroll", "Зарплатный проект в Альфа-Банке"),
)
BORROWING = {BankProduct.LOAN, BankProduct.LEASING, BankProduct.GUARANTEE, BankProduct.FACTORING}
BORROWING_TITLES = {
    BankProduct.LOAN: "кредиты",
    BankProduct.LEASING: "лизинг",
    BankProduct.GUARANTEE: "гарантии",
    BankProduct.FACTORING: "факторинг",
}

# Перечень недружественных государств (распоряжение Правительства РФ № 430-р с изменениями).
UNFRIENDLY = frozenset(
    {
        "США", "ВЕЛИКОБРИТАНИЯ", "СОЕДИНЕННОЕ КОРОЛЕВСТВО", "АВСТРИЯ", "БЕЛЬГИЯ", "БОЛГАРИЯ",
        "ВЕНГРИЯ", "ГЕРМАНИЯ", "ГРЕЦИЯ", "ДАНИЯ", "ИРЛАНДИЯ", "ИСПАНИЯ", "ИТАЛИЯ", "КИПР",
        "ЛАТВИЯ", "ЛИТВА", "ЛЮКСЕМБУРГ", "МАЛЬТА", "НИДЕРЛАНДЫ", "ПОЛЬША", "ПОРТУГАЛИЯ",
        "РУМЫНИЯ", "СЛОВАКИЯ", "СЛОВЕНИЯ", "ФИНЛЯНДИЯ", "ФРАНЦИЯ", "ХОРВАТИЯ", "ЧЕХИЯ",
        "ШВЕЦИЯ", "ЭСТОНИЯ", "УКРАИНА", "ЧЕРНОГОРИЯ", "ШВЕЙЦАРИЯ", "АЛБАНИЯ", "АНДОРРА",
        "ИСЛАНДИЯ", "ЛИХТЕНШТЕЙН", "МОНАКО", "НОРВЕГИЯ", "САН-МАРИНО", "СЕВЕРНАЯ МАКЕДОНИЯ",
        "ЯПОНИЯ", "ЮЖНАЯ КОРЕЯ", "АВСТРАЛИЯ", "МИКРОНЕЗИЯ", "НОВАЯ ЗЕЛАНДИЯ", "СИНГАПУР",
        "ТАЙВАНЬ", "КАНАДА", "БАГАМЫ",
    }
)  # fmt: skip

# Страны, через которые идёт основная часть расчётов по ВЭД и где банки-корреспонденты
# осторожничают из-за вторичных санкций: задержки и возвраты платежей.
PAYMENT_HUBS = frozenset({"КИТАЙ", "ГОНКОНГ", "ТУРЦИЯ", "ОАЭ", "КАЗАХСТАН", "КИРГИЗИЯ", "АРМЕНИЯ"})

# Валюта страны в родительном падеже — для запроса «курс юаня».
CURRENCIES = {
    "КИТАЙ": "юаня",
    "ГОНКОНГ": "гонконгского доллара",
    "ТУРЦИЯ": "турецкой лиры",
    "ОАЭ": "дирхама",
    "ИНДИЯ": "рупии",
    "КАЗАХСТАН": "тенге",
    "БЕЛАРУСЬ": "белорусского рубля",
    "УЗБЕКИСТАН": "сума",
    "КИРГИЗИЯ": "сома",
    "АРМЕНИЯ": "драма",
    "США": "доллара",
}
EURO_AREA = frozenset(
    {"АВСТРИЯ", "БЕЛЬГИЯ", "ГЕРМАНИЯ", "ГРЕЦИЯ", "ИРЛАНДИЯ", "ИСПАНИЯ", "ИТАЛИЯ", "КИПР",
     "ЛАТВИЯ", "ЛИТВА", "ЛЮКСЕМБУРГ", "МАЛЬТА", "НИДЕРЛАНДЫ", "ПОРТУГАЛИЯ", "СЛОВАКИЯ",
     "СЛОВЕНИЯ", "ФИНЛЯНДИЯ", "ФРАНЦИЯ", "ХОРВАТИЯ", "ЭСТОНИЯ"}
)  # fmt: skip


@dataclass
class ProductProfile:
    products: list[BankProduct]
    foreign_trade: ForeignTrade
    features: list[ProfileFeature] = field(default_factory=list)
    exposures: list[Exposure] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def country_title(name: str) -> str:
    return name if len(name) <= 3 else name.title()


def currency(country: str) -> str | None:
    return "евро" if country in EURO_AREA else CURRENCIES.get(country)


def sentence(text: str) -> str:
    """Заглавная только первая буква: str.capitalize() сломал бы названия стран."""
    return text[:1].upper() + text[1:]


def listing(names: list[str]) -> str:
    return ", ".join(country_title(name) for name in names)


def product_profile(row: CompanyRow) -> ProductProfile:
    features: list[ProfileFeature] = []
    exposures: list[Exposure] = []
    missing: list[str] = []
    warnings: list[str] = []

    def add(
        id_: str,
        kind: FeatureKind,
        label: str,
        value: str | bool,
        *columns: str,
        derived: bool = False,
    ) -> str:
        feature_id = f"feature:{id_}"
        features.append(
            ProfileFeature(
                id=feature_id,
                kind=kind,
                label=label,
                value=value,
                source_fields=[source_field(column) for column in columns],
                derived=derived,
            )
        )
        return feature_id

    products: list[BankProduct] = []
    feature_of: dict[BankProduct, str] = {}
    for product, column, label in PRODUCTS:
        flag = getattr(row, column)
        if flag is None:
            missing.append(source_field(column))
        elif flag:
            products.append(product)
            feature_of[product] = add(product.value, FeatureKind.PRODUCT, label, True, column)

    if not products and row.client_type == "PROSPECT":
        warnings.append(
            "Потенциальный клиент: продуктов в банке нет, о долге и депозитах в других банках "
            "по выгрузке судить нельзя"
        )

    borrowing = [product for product in products if product in BORROWING]
    if borrowing or BankProduct.DEPOSIT in products:
        reasons = []
        if borrowing:
            titles = ", ".join(BORROWING_TITLES[product] for product in borrowing)
            reasons.append(f"{titles} в банке: ставка определяет стоимость долга")
        if BankProduct.DEPOSIT in products:
            reasons.append("депозиты: от ставки зависит доходность свободных денег")
        direction = (
            ExposureDirection.MIXED
            if borrowing and BankProduct.DEPOSIT in products
            else ExposureDirection.RISK
            if borrowing
            else ExposureDirection.OPPORTUNITY
        )
        exposures.append(
            Exposure(
                topic=Topic.KEY_RATE,
                direction=direction,
                reason=sentence("; ".join(reasons)),
                feature_ids=[
                    feature_of[product]
                    for product in (*borrowing, BankProduct.DEPOSIT)
                    if product in feature_of
                ],
                keywords=["ключевая ставка", "стоимость кредитов", "ставки по депозитам"],
            )
        )

    if BankProduct.PAYROLL in products:
        exposures.append(
            Exposure(
                topic=Topic.LABOR,
                direction=ExposureDirection.RISK,
                reason="Зарплатный проект: на компанию влияют рост зарплат, НДФЛ и дефицит кадров",
                feature_ids=[feature_of[BankProduct.PAYROLL]],
                keywords=["зарплаты", "рынок труда", "дефицит кадров", "ндфл"],
            )
        )

    countries = list(row.customs_countries)
    has_customs = bool(row.has_customs_records) or bool(countries)
    if row.has_customs_records is None and not countries:
        missing.append(source_field("has_customs_records"))
    if row.has_customs_records is False and countries:
        warnings.append("Страны ФТС указаны при FTS_FLAG = 0: ВЭД считаем подтверждённой")
    if row.has_customs_records and not countries:
        missing.append(source_field("customs_countries"))

    if has_customs:
        where = f": {listing(countries)}" if countries else ""
        customs = add(
            "customs",
            FeatureKind.FOREIGN_TRADE,
            f"Ведёт внешнеэкономическую деятельность{where}",
            ", ".join(countries) if countries else True,
            "has_customs_records",
            *(["customs_countries"] if countries else []),
        )
        currencies = list(dict.fromkeys(filter(None, map(currency, countries))))
        exposures.append(
            Exposure(
                topic=Topic.FX,
                direction=ExposureDirection.MIXED,
                reason=(
                    "Расчёты с иностранными партнёрами: курс меняет стоимость закупок или "
                    "выручку от экспорта. Направление ВЭД по данным ФТС неизвестно"
                ),
                feature_ids=[customs],
                keywords=["курс рубля", *(f"курс {name}" for name in currencies)],
            )
        )
        exposures.append(
            Exposure(
                topic=Topic.TRADE,
                direction=ExposureDirection.MIXED,
                reason="Пошлины, таможенные правила и логистика напрямую меняют условия ВЭД",
                feature_ids=[customs],
                keywords=[
                    "таможенные пошлины",
                    "таможня",
                    "параллельный импорт",
                    *(country_title(name) for name in countries),
                ],
            )
        )
        sanction_features = [customs]
        sanction_reasons = []
        unfriendly = [name for name in countries if name in UNFRIENDLY]
        hubs = [name for name in countries if name in PAYMENT_HUBS]
        if unfriendly:
            sanction_features.append(
                add(
                    "unfriendly_trade",
                    FeatureKind.FOREIGN_TRADE,
                    f"Торгует со странами из перечня недружественных: {listing(unfriendly)}",
                    ", ".join(unfriendly),
                    "customs_countries",
                    derived=True,
                )
            )
            sanction_reasons.append(f"ограничения в торговле со странами: {listing(unfriendly)}")
        if hubs:
            sanction_features.append(
                add(
                    "payment_hub_trade",
                    FeatureKind.FOREIGN_TRADE,
                    f"Расчёты через страны с риском задержки платежей: {listing(hubs)}",
                    ", ".join(hubs),
                    "customs_countries",
                    derived=True,
                )
            )
            sanction_reasons.append(
                f"вторичные санкции тормозят платежи партнёрам: {listing(hubs)}"
            )
        if sanction_reasons:
            exposures.append(
                Exposure(
                    topic=Topic.SANCTIONS,
                    direction=ExposureDirection.RISK,
                    reason=sentence("; ".join(sanction_reasons)),
                    feature_ids=sanction_features,
                    keywords=["санкции", "вторичные санкции", "платежи за рубеж"],
                )
            )

    return ProductProfile(
        products=products,
        foreign_trade=ForeignTrade(has_customs_records=has_customs, countries=countries),
        features=features,
        exposures=exposures,
        missing_fields=missing,
        warnings=warnings,
    )
