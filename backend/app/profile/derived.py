"""Производные признаки: зависимость от заёмных средств, направление ВЭД, концентрация на
крупном контрагенте и география присутствия. Их агент берёт в объяснение «почему это важно вам».

Это оценки поверх данных, а не данные, поэтому все признаки помечены как вычисленные, а
формулировки — осторожные («вероятно импортёр»). Уточнённые здесь темы (ставка, валюта)
заменяют грубые из продуктового профиля: см. `merge_exposures`.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum

from app.contracts.common import BankProduct
from app.contracts.news import Topic
from app.contracts.profile import (
    Counterparty,
    Exposure,
    ExposureDirection,
    FeatureKind,
    ProfileFeature,
    ValueChain,
)
from app.profile.features import BaseFeatures
from app.profile.graph import PAYMENT_FIELDS, PERSON_FIELDS, LinkProfile
from app.profile.okved import resolve
from app.profile.products import BORROWING, BORROWING_TITLES, ProductProfile
from app.profile.row import CompanyRow, source_field

HIGH_CONCENTRATION = 0.5
MEDIUM_CONCENTRATION = 0.25
FINANCE_SHARE_HIGH = 0.1
DIRECTION_BY_CHAIN = {
    ValueChain.WHOLESALE: "import",
    ValueChain.RETAIL: "import",
    ValueChain.EXTRACTION: "export",
    ValueChain.MANUFACTURING: "both",
}
DIRECTION_TITLES = {
    "import": "Вероятно импортёр",
    "export": "Вероятно экспортёр",
    "both": "Вероятно и закупает, и продаёт за рубежом",
}
DIRECTION_REASONS = {
    "import": "торговая компания с ВЭД обычно ввозит товар",
    "export": "добывающая компания с ВЭД обычно вывозит сырьё",
    "both": "производитель с ВЭД обычно ввозит комплектующие и вывозит продукцию",
}
FX_EXPOSURES = {
    "import": (
        ExposureDirection.RISK,
        "Ослабление рубля удорожает закупки за рубежом и сжимает маржу",
    ),
    "export": (
        ExposureDirection.OPPORTUNITY,
        "Ослабление рубля увеличивает рублёвую выручку от экспорта, укрепление — снижает",
    ),
    "both": (
        ExposureDirection.MIXED,
        "Курс одновременно меняет стоимость закупок и рублёвую выручку от экспорта",
    ),
}
CONCENTRATION_ROLES = {
    "incoming": ("customer", "покупатель", "выручки"),
    "outgoing": ("supplier", "поставщик", "закупок"),
}


class Level(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


@dataclass
class DerivedProfile:
    features: list[ProfileFeature] = field(default_factory=list)
    exposures: list[Exposure] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add(
        self, id_: str, kind: FeatureKind, label: str, value: str | float, fields: list[str]
    ) -> str:
        feature_id = f"feature:{id_}"
        self.features.append(
            ProfileFeature(
                id=feature_id,
                kind=kind,
                label=label,
                value=value,
                source_fields=list(dict.fromkeys(fields)),
                derived=True,
            )
        )
        return feature_id


def merge_exposures(*groups: Iterable[Exposure]) -> list[Exposure]:
    """Склеивает чувствительность из разных блоков профиля. По одной теме остаётся последняя
    оценка: уточнённая из производных признаков заменяет грубую из продуктов. Отраслевые темы
    не заменяются — у каждой отрасли окружения своя."""
    merged: dict[object, Exposure] = {}
    for group in groups:
        for exposure in group:
            key = (
                (exposure.topic, exposure.reason)
                if exposure.topic is Topic.INDUSTRY
                else exposure.topic
            )
            merged.pop(key, None)
            merged[key] = exposure
    return list(merged.values())


def percent(share: float) -> str:
    return f"{share * 100:.0f}%"


def derive(
    row: CompanyRow, base: BaseFeatures, products: ProductProfile, links: LinkProfile
) -> DerivedProfile:
    result = DerivedProfile()
    debt(result, products, links)
    trade_direction(result, row, products)
    for direction in ("incoming", "outgoing"):
        concentration(result, direction, links.counterparties)
    geography(result, base, links)
    return result


def debt(result: DerivedProfile, products: ProductProfile, links: LinkProfile) -> None:
    borrowing = [product for product in products.products if product in BORROWING]
    finance_share = sum(
        item.share or 0
        for item in links.counterparties
        if item.direction == "outgoing" and item.segment == "finance"
    )
    evidence = [f"feature:{product.value}" for product in borrowing]
    reasons = []
    fields = [source_field(f"has_{product.value}") for product in borrowing]
    if borrowing:
        reasons.append(f"{', '.join(BORROWING_TITLES[product] for product in borrowing)} в банке")
    if finance_share > 0:
        payments = result.add(
            "finance_payments",
            FeatureKind.FINANCE,
            f"Платежи финансовым и лизинговым компаниям: {percent(finance_share)} исходящих",
            finance_share,
            PAYMENT_FIELDS,
        )
        evidence.append(payments)
        reasons.append(
            f"{percent(finance_share)} исходящих платежей уходит финансовым компаниям — "
            "вероятно, обслуживание долга и лизинга, в том числе в других банках"
        )
        fields += PAYMENT_FIELDS
    if not evidence:
        return
    level = (
        Level.HIGH if len(borrowing) >= 2 or finance_share >= FINANCE_SHARE_HIGH else Level.MEDIUM
    )
    title = "высокая" if level is Level.HIGH else "заметная"
    feature = result.add(
        "debt_dependence",
        FeatureKind.FINANCE,
        f"Зависимость от заёмных средств: {title}",
        level.value,
        fields,
    )
    result.exposures.append(
        Exposure(
            topic=Topic.KEY_RATE,
            direction=ExposureDirection.MIXED
            if BankProduct.DEPOSIT in products.products
            else ExposureDirection.RISK,
            reason=f"Зависимость от заёмных средств {title}: {'; '.join(reasons)}. "
            "Ставка определяет стоимость обслуживания долга",
            feature_ids=[
                feature,
                *evidence,
                *(["feature:deposit"] if BankProduct.DEPOSIT in products.products else []),
            ],
            keywords=["ключевая ставка", "стоимость кредитов", "ставки по кредитам", "лизинг"],
        )
    )


def trade_direction(result: DerivedProfile, row: CompanyRow, products: ProductProfile) -> None:
    if not products.foreign_trade.has_customs_records:
        return
    chain = resolve(row.okved_code, row.okved_name).value_chain
    direction = DIRECTION_BY_CHAIN.get(chain) if chain else None
    if direction is None:
        result.warnings.append("Направление ВЭД не определить: отрасль не подсказывает его")
        return
    feature = result.add(
        "trade_direction",
        FeatureKind.FOREIGN_TRADE,
        f"{DIRECTION_TITLES[direction]} (оценка: {DIRECTION_REASONS[direction]})",
        direction,
        [source_field("has_customs_records"), source_field("okved_code")],
    )
    fx = next((item for item in products.exposures if item.topic is Topic.FX), None)
    if fx is None:
        return
    exposure_direction, reason = FX_EXPOSURES[direction]
    result.exposures.append(
        Exposure(
            topic=Topic.FX,
            direction=exposure_direction,
            reason=f"{reason}. {DIRECTION_TITLES[direction]}: {DIRECTION_REASONS[direction]}",
            feature_ids=[*fx.feature_ids, feature],
            keywords=fx.keywords,
        )
    )


def concentration(
    result: DerivedProfile, direction: str, counterparties: list[Counterparty]
) -> None:
    shares = sorted(
        (item.share for item in counterparties if item.direction == direction and item.share),
        reverse=True,
    )
    if not shares:
        return
    id_, role, base = CONCENTRATION_ROLES[direction]
    largest = shares[0]
    level = (
        Level.HIGH
        if largest >= HIGH_CONCENTRATION
        else Level.MEDIUM
        if largest >= MEDIUM_CONCENTRATION
        else Level.LOW
    )
    feature = result.add(
        f"{id_}_concentration",
        FeatureKind.COUNTERPARTY,
        f"Крупнейший {role} — {percent(largest)} {base}, "
        f"три крупнейших — {percent(sum(shares[:3]))}",
        level.value,
        PAYMENT_FIELDS,
    )
    if level is not Level.LOW:
        result.exposures.append(
            Exposure(
                topic=Topic.COMPANY,
                direction=ExposureDirection.RISK,
                reason=f"Крупнейший {role} даёт {percent(largest)} {base}: новости о нём, "
                "его проблемы или смена условий напрямую бьют по компании",
                feature_ids=[feature],
            )
        )


def geography(result: DerivedProfile, base: BaseFeatures, links: LinkProfile) -> None:
    home = base.location.region
    partner_regions = [
        item.region for item in links.counterparties if item.region and item.region != home
    ]
    group_regions = [
        member.region
        for member in (links.group.members if links.group else [])
        if member.region and member.region != home
    ]
    regions = list(dict.fromkeys([*partner_regions, *group_regions]))
    parts = []
    fields = [source_field("region")]
    if home:
        parts.append(f"регистрация — {home}")
    if regions:
        parts.append(f"контрагенты и компании группы — {', '.join(regions[:5])}")
        fields += ["REGION", "PAYER", "BENEFICIARY", "GROUP_ID"]
    if links.person_regions:
        parts.append(f"связанные лица — {', '.join(links.person_regions)}")
        fields += PERSON_FIELDS
    if len(parts) < 2:
        return
    feature = result.add(
        "geography",
        FeatureKind.REGION,
        "География присутствия: " + "; ".join(parts),
        len(regions) + 1,
        fields,
    )
    result.exposures.append(
        Exposure(
            topic=Topic.REGIONAL,
            direction=ExposureDirection.MIXED,
            reason="Региональные события касаются компании не только в регионе регистрации: "
            + "; ".join(parts),
            feature_ids=[feature],
            keywords=[region for region in (home, *regions) if region],
        )
    )
