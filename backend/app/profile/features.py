"""Базовые признаки компании: выручка и размер, сотрудники, возраст, стадия, собственность,
география и тип клиента. Каждый признак знает поле выгрузки, из которого получен."""

import re
from dataclasses import dataclass, field
from decimal import Decimal

from app.contracts.profile import (
    ClientType,
    FeatureKind,
    Location,
    ProfileFeature,
    Scale,
    SizeCategory,
)
from app.profile.row import CompanyRow, source_field

LARGE_BUSINESS_RUB = Decimal(15_000_000_000)
SIZE_LIMITS = (
    (Decimal(120_000_000), SizeCategory.MICRO),
    (Decimal(800_000_000), SizeCategory.SMALL),
    (Decimal(2_000_000_000), SizeCategory.MEDIUM),
)
SIZE_TITLES = {
    SizeCategory.MICRO: "микропредприятие по выручке",
    SizeCategory.SMALL: "малый бизнес по выручке",
    SizeCategory.MEDIUM: "средний бизнес по выручке",
    SizeCategory.LARGE: "крупный бизнес по выручке",
    SizeCategory.LARGEST: "крупнейший бизнес: выручка от 15 млрд ₽",
}
CLIENT_TITLES = {
    ClientType.CLIENT: "действующий клиент банка",
    ClientType.CLOSE_CLIENT: "бывший клиент банка",
    ClientType.PROSPECT: "потенциальный клиент банка",
}
SMALL_STAFF_BANDS = {"Менее 15"}
FOREIGN_OWNERSHIP = re.compile(r"иностран")
STATE_OWNERSHIP = re.compile(r"государствен|федеральн|субъект|муниципальн")
CITY_PREFIX = re.compile(r"^(г\.|г |город )\s*", re.IGNORECASE)
BAND = re.compile(r"^(\d+)-(\d+)$")


@dataclass
class BaseFeatures:
    scale: Scale
    location: Location
    ownership: str | None
    client_type: ClientType | None
    features: list[ProfileFeature] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def money(value: Decimal) -> str:
    for limit, suffix in (
        (Decimal(10**9), "млрд"),
        (Decimal(10**6), "млн"),
        (Decimal(10**3), "тыс"),
    ):
        if value >= limit:
            amount = f"{value / limit:.1f}".removesuffix(".0").replace(".", ",")
            return f"{amount} {suffix} ₽"
    return f"{value:.0f} ₽"


def band(value: str) -> str:
    """«8-15» → «8–15»: градации выгрузки показываем по правилам типографики."""
    return BAND.sub(r"\1–\2", value)


def size_category(revenue: Decimal) -> SizeCategory:
    if revenue >= LARGE_BUSINESS_RUB:
        return SizeCategory.LARGEST
    return next((size for limit, size in SIZE_LIMITS if revenue <= limit), SizeCategory.LARGE)


def city_name(raw: str | None) -> str | None:
    if raw is None:
        return None
    return CITY_PREFIX.sub("", raw).strip() or None


def client_type(raw: str | None) -> ClientType | None:
    try:
        return ClientType(raw.lower()) if raw else None
    except ValueError:
        return None


def base_features(row: CompanyRow) -> BaseFeatures:
    features: list[ProfileFeature] = []
    missing: list[str] = []
    warnings: list[str] = []

    def add(
        id_: str,
        kind: FeatureKind,
        label: str,
        value: str | float | bool,
        *columns: str,
        derived: bool = False,
    ) -> None:
        features.append(
            ProfileFeature(
                id=f"feature:{id_}",
                kind=kind,
                label=label,
                value=value,
                source_fields=[source_field(column) for column in columns],
                derived=derived,
            )
        )

    revenue = row.revenue_rub if row.revenue_rub else None
    category = size_category(revenue) if revenue is not None else None
    if revenue is not None and category is not None:
        add(
            "revenue",
            FeatureKind.FINANCE,
            f"Выручка {money(revenue)} в год",
            float(revenue),
            "revenue_rub",
        )
        add(
            "size",
            FeatureKind.SCALE,
            SIZE_TITLES[category].capitalize(),
            category.value,
            "revenue_rub",
            derived=True,
        )
    else:
        missing.append(source_field("revenue_rub"))
        if row.revenue_rub == 0:
            warnings.append("Выручка равна нулю: размер компании не определён")

    employees, employees_source = row.employees_fns, "employees_fns"
    if employees is None and row.employees_spark is not None:
        employees, employees_source = row.employees_spark, "employees_spark"
    if row.employees_fns and row.employees_spark and row.employees_fns != row.employees_spark:
        warnings.append("Численность по ФНС и СПАРК расходится, взята ФНС")
    if employees is not None:
        origin = "ФНС" if employees_source == "employees_fns" else "СПАРК"
        add(
            "employees",
            FeatureKind.SCALE,
            f"Сотрудников: {band(employees)} (по данным {origin})",
            employees,
            employees_source,
        )
        if category is SizeCategory.LARGEST and employees in SMALL_STAFF_BANDS:
            warnings.append(
                "Выручка крупнейшего бизнеса при штате менее 15 человек: "
                "возможно, холдинг или ошибка в данных"
            )
    else:
        missing.append(source_field("employees_fns"))

    if row.age_band is not None:
        add(
            "age",
            FeatureKind.LIFECYCLE,
            f"Возраст компании: {band(row.age_band)} лет",
            row.age_band,
            "age_band",
        )
    else:
        missing.append(source_field("age_band"))

    if row.lifecycle_stage is not None:
        add(
            "stage",
            FeatureKind.LIFECYCLE,
            f"Стадия развития: {row.lifecycle_stage.lower()}",
            row.lifecycle_stage,
            "lifecycle_stage",
        )
    else:
        missing.append(source_field("lifecycle_stage"))

    ownership = row.ownership.capitalize() if row.ownership else None
    if ownership is not None:
        add(
            "ownership",
            FeatureKind.OWNERSHIP,
            f"Форма собственности: {ownership.lower()}",
            ownership,
            "ownership",
        )
        lowered = ownership.lower()
        if FOREIGN_OWNERSHIP.search(lowered):
            add(
                "foreign_capital",
                FeatureKind.OWNERSHIP,
                "Есть иностранный капитал",
                True,
                "ownership",
                derived=True,
            )
        if STATE_OWNERSHIP.search(lowered):
            add(
                "state_capital",
                FeatureKind.OWNERSHIP,
                "Есть государственное или муниципальное участие",
                True,
                "ownership",
                derived=True,
            )
    else:
        missing.append(source_field("ownership"))

    city = city_name(row.city)
    if row.region is not None:
        add("region", FeatureKind.REGION, f"Регион регистрации: {row.region}", row.region, "region")
    else:
        missing.append(source_field("region"))
    if city is not None:
        add("city", FeatureKind.REGION, f"Город регистрации: {city}", city, "city")
    else:
        missing.append(source_field("city"))

    kind = client_type(row.client_type)
    if kind is not None:
        add(
            "client_type",
            FeatureKind.PRODUCT,
            CLIENT_TITLES[kind].capitalize(),
            kind.value,
            "client_type",
        )
    else:
        missing.append(source_field("client_type"))

    scale = Scale(
        revenue_rub=float(revenue) if revenue is not None else None,
        size_category=category,
        employees_band=employees,
        age_band=row.age_band,
        lifecycle_stage=row.lifecycle_stage,
        is_large_business=(revenue >= LARGE_BUSINESS_RUB) if revenue is not None else None,
    )
    return BaseFeatures(
        scale=scale,
        location=Location(city=city, region=row.region),
        ownership=ownership,
        client_type=kind,
        features=features,
        missing_fields=missing,
        warnings=warnings,
    )
