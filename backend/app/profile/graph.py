"""Граф связей компании: группа (GROUP_ID), контрагенты по платежам и связанные физлица.

Главная польза — отрасли окружения: новость про рынок крупного покупателя или поставщика важнее
общеотраслевой. Поэтому сегменты контрагентов и группы превращаются в чувствительность
к отраслевым новостям (`Exposure` с темой industry).
"""

from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

from app.contracts.news import Topic
from app.contracts.profile import (
    CompanyGroup,
    Counterparty,
    Exposure,
    ExposureDirection,
    FeatureKind,
    ProfileFeature,
    RelatedCompany,
)
from app.profile.okved import SEGMENTS, resolve
from app.profile.row import CompanyRow, source_field

TOP_MEMBERS = 10
TOP_COUNTERPARTIES = 10
TOP_RELATED = 20
TOP_GROUP_SEGMENTS = 3
SIGNIFICANT_SHARE = Decimal("0.1")
PAYMENT_FIELDS = ["PAYER", "BENEFICIARY", "PAY_AMT", "PAY_CNT"]
PERSON_FIELDS = ["SYNTHETIC_F", "SYNTHETIC_U", "RC_NAME"]

Direction = Literal["incoming", "outgoing"]
ROLES = {"incoming": "покупатели", "outgoing": "поставщики"}
CONSEQUENCES = {
    "incoming": "спад на их рынке ударит по вашей выручке",
    "outgoing": "рост цен и перебои у них отразятся на ваших затратах",
}

GROUP_SQL = """
SELECT company_id, okved_code, okved_name, region, revenue_rub
FROM companies WHERE group_id = $1 AND company_id <> $2
"""
PAYMENTS_SQL = """
SELECT 'incoming' AS direction, p.payer_id AS partner_id, p.amount_rub, p.payments_count,
       c.okved_code, c.okved_name, c.region, c.revenue_rub
FROM payments p LEFT JOIN companies c ON c.company_id = p.payer_id
WHERE p.beneficiary_id = $1
UNION ALL
SELECT 'outgoing', p.beneficiary_id, p.amount_rub, p.payments_count,
       c.okved_code, c.okved_name, c.region, c.revenue_rub
FROM payments p LEFT JOIN companies c ON c.company_id = p.beneficiary_id
WHERE p.payer_id = $1
"""
PERSONS_SQL = "SELECT person_id, region FROM persons WHERE company_id = $1"
SHARED_PERSON_SQL = """
SELECT DISTINCT other.company_id, c.okved_code, c.okved_name, c.region, c.revenue_rub
FROM persons own
JOIN persons other ON other.person_id = own.person_id AND other.company_id <> own.company_id
LEFT JOIN companies c ON c.company_id = other.company_id
WHERE own.company_id = $1
ORDER BY other.company_id
LIMIT $2
"""
SHARED_PERSON_COUNT_SQL = """
SELECT count(DISTINCT other.company_id)
FROM persons own
JOIN persons other ON other.person_id = own.person_id AND other.company_id <> own.company_id
WHERE own.company_id = $1
"""


@dataclass(frozen=True)
class LinkedCompany:
    company_id: str
    okved_code: str | None = None
    okved_name: str | None = None
    region: str | None = None
    revenue_rub: Decimal | None = None

    @property
    def segment(self) -> str | None:
        segment = resolve(self.okved_code, self.okved_name).segment.id
        return None if segment == "other" else segment

    def related(self, relation: Literal["group", "shared_person"]) -> RelatedCompany:
        return RelatedCompany(
            company_id=self.company_id,
            relation=relation,
            okved_name=self.okved_name,
            segment=self.segment,
            region=self.region,
            revenue_rub=float(self.revenue_rub) if self.revenue_rub is not None else None,
        )


@dataclass(frozen=True)
class Payment:
    partner: LinkedCompany
    direction: Direction
    amount_rub: Decimal
    payments_count: int | None = None


@dataclass
class CompanyLinks:
    group_members: list[LinkedCompany] = field(default_factory=list)
    payments: list[Payment] = field(default_factory=list)
    person_regions: list[str | None] = field(default_factory=list)
    shared_person_companies: list[LinkedCompany] = field(default_factory=list)
    shared_person_total: int = 0


@dataclass
class LinkProfile:
    group: CompanyGroup | None = None
    counterparties: list[Counterparty] = field(default_factory=list)
    linked_persons_count: int = 0
    person_regions: list[str] = field(default_factory=list)
    related_companies: list[RelatedCompany] = field(default_factory=list)
    features: list[ProfileFeature] = field(default_factory=list)
    exposures: list[Exposure] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def linked(record: Any, id_column: str = "company_id") -> LinkedCompany:
    return LinkedCompany(
        company_id=record[id_column],
        okved_code=record["okved_code"],
        okved_name=record["okved_name"],
        region=record["region"],
        revenue_rub=record["revenue_rub"],
    )


async def fetch_links(connection: Any, row: CompanyRow) -> CompanyLinks:
    """Связи компании из таблиц загрузчика A2-01."""
    members = (
        await connection.fetch(GROUP_SQL, row.group_id, row.company_id) if row.group_id else []
    )
    payments = await connection.fetch(PAYMENTS_SQL, row.company_id)
    persons = await connection.fetch(PERSONS_SQL, row.company_id)
    shared = await connection.fetch(SHARED_PERSON_SQL, row.company_id, TOP_RELATED)
    shared_total = await connection.fetchval(SHARED_PERSON_COUNT_SQL, row.company_id)
    return CompanyLinks(
        group_members=[linked(record) for record in members],
        payments=[
            Payment(
                partner=linked(record, "partner_id"),
                direction=record["direction"],
                amount_rub=record["amount_rub"],
                payments_count=record["payments_count"],
            )
            for record in payments
        ],
        person_regions=[record["region"] for record in persons],
        shared_person_companies=[linked(record) for record in shared],
        shared_person_total=shared_total,
    )


def segment_title(segment_id: str) -> str:
    return SEGMENTS[segment_id].name


def ranked_segments(companies: Sequence[LinkedCompany]) -> list[str]:
    counts = Counter(company.segment for company in companies if company.segment)
    return [segment for segment, _ in counts.most_common()]


def by_revenue(company: LinkedCompany) -> tuple[bool, Decimal]:
    return company.revenue_rub is not None, company.revenue_rub or Decimal(0)


def percent(share: Decimal) -> str:
    return f"{share * 100:.0f}%"


def link_profile(row: CompanyRow, links: CompanyLinks) -> LinkProfile:
    result = LinkProfile()
    own_segment = resolve(row.okved_code, row.okved_name).segment.id

    def add(id_: str, kind: FeatureKind, label: str, value: str | float, fields: list[str]) -> str:
        feature_id = f"feature:{id_}"
        result.features.append(
            ProfileFeature(
                id=feature_id,
                kind=kind,
                label=label,
                value=value,
                source_fields=fields,
                derived=True,
            )
        )
        return feature_id

    def industry_exposure(segment_id: str, reason: str, feature_id: str) -> None:
        result.exposures.append(
            Exposure(
                topic=Topic.INDUSTRY,
                direction=ExposureDirection.MIXED,
                reason=reason,
                feature_ids=[feature_id],
                keywords=list(SEGMENTS[segment_id].keywords),
            )
        )

    if row.group_id is None:
        result.missing_fields.append(source_field("group_id"))
    elif links.group_members:
        segments = ranked_segments(links.group_members)
        members = sorted(links.group_members, key=by_revenue, reverse=True)[:TOP_MEMBERS]
        result.group = CompanyGroup(
            group_id=row.group_id,
            companies_count=len(links.group_members) + 1,
            industries=[segment_title(segment) for segment in segments],
            members=[member.related("group") for member in members],
        )
        add(
            "group",
            FeatureKind.GROUP,
            f"Входит в группу из {result.group.companies_count} компаний",
            result.group.companies_count,
            [source_field("group_id")],
        )
        others = [segment for segment in segments if segment != own_segment][:TOP_GROUP_SEGMENTS]
        if others:
            titles = ", ".join(f"«{segment_title(segment)}»" for segment in others)
            segment_feature = add(
                "group_segments",
                FeatureKind.GROUP,
                f"Компании группы работают и в других отраслях: {titles}",
                ", ".join(others),
                [source_field("group_id"), source_field("okved_code")],
            )
            for segment in others:
                industry_exposure(
                    segment,
                    f"Компании группы работают в отрасли «{segment_title(segment)}»: "
                    "события этого рынка касаются всей группы",
                    segment_feature,
                )

    for direction in ("incoming", "outgoing"):
        payments = [payment for payment in links.payments if payment.direction == direction]
        total = sum((payment.amount_rub for payment in payments), Decimal(0))
        if not payments or total <= 0:
            continue
        top = sorted(payments, key=lambda payment: payment.amount_rub, reverse=True)
        for payment in top[:TOP_COUNTERPARTIES]:
            result.counterparties.append(
                Counterparty(
                    company_id=payment.partner.company_id,
                    direction=direction,
                    amount_rub=float(payment.amount_rub),
                    payments_count=payment.payments_count,
                    share=float(payment.amount_rub / total),
                    okved_name=payment.partner.okved_name,
                    segment=payment.partner.segment,
                    region=payment.partner.region,
                )
            )
        largest = top[0].amount_rub / total
        role = ROLES[direction]
        partners_feature = add(
            f"counterparties_{direction}",
            FeatureKind.COUNTERPARTY,
            f"{role.capitalize()} по платежам: {len(payments)}, "
            f"крупнейший — {percent(largest)} оборота",
            len(payments),
            PAYMENT_FIELDS,
        )
        shares: defaultdict[str, Decimal] = defaultdict(Decimal)
        for payment in payments:
            if payment.partner.segment:
                shares[payment.partner.segment] += payment.amount_rub / total
        for segment, share in sorted(shares.items(), key=lambda item: item[1], reverse=True):
            if share < SIGNIFICANT_SHARE:
                break
            industry_exposure(
                segment,
                f"Ваши {role} из отрасли «{segment_title(segment)}» дают {percent(share)} "
                f"оборота: {CONSEQUENCES[direction]}",
                partners_feature,
            )
    if not links.payments:
        result.warnings.append("Платежей нет: контрагенты не определены")

    regions = list(dict.fromkeys(region for region in links.person_regions if region))
    result.linked_persons_count = len(links.person_regions)
    result.person_regions = regions
    if links.person_regions:
        where = f", регионы: {', '.join(regions)}" if regions else ""
        add(
            "persons",
            FeatureKind.GROUP,
            f"Связанных физлиц: {len(links.person_regions)}{where}",
            len(links.person_regions),
            PERSON_FIELDS,
        )
    if links.shared_person_total:
        result.related_companies = [
            company.related("shared_person") for company in links.shared_person_companies
        ]
        add(
            "person_links",
            FeatureKind.GROUP,
            f"Через общих физлиц связана ещё с {links.shared_person_total} компаниями",
            links.shared_person_total,
            PERSON_FIELDS,
        )
    return result
