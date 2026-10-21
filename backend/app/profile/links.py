"""Связанные компании (A2-16): карточки контрагентов по платежам, компаний группы (GROUP_ID)
и компаний, связанных через общих физлиц. Доля в обороте считается среди всех платежей
направления: по сумме (PAY_AMT) и по числу платежей (PAY_CNT).

Тип связи — покупатель, поставщик, перевозчик — задаёт клиент. Его ответ лежит в базе знаний
фактом `counterparty.<id>.role`: у правки есть история, и клиент главнее легенды. Пока клиент
не ответил, тип берётся из легенды, а без неё — по направлению платежей, и помечен догадкой.

Компанию, которой нет в данных банка, клиент добавляет руками. Она хранится в таблице
manual_links и попадает в профиль признаком и отраслевой чувствительностью, если клиент
назвал её отрасль.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from app.contracts.knowledge import FactSource, KnowledgeBase, KnowledgeFact
from app.contracts.links import (
    LinkedCompanies,
    LinkedCompanyCard,
    LinkOrigin,
    PaymentTotals,
    RoleSource,
    Turnover,
)
from app.contracts.news import Topic
from app.contracts.profile import (
    CompanyProfile,
    CounterpartyRole,
    Exposure,
    ExposureDirection,
    FeatureKind,
    ProfileFeature,
)
from app.profile.builder import COMPANY_SQL
from app.profile.graph import (
    SIGNIFICANT_SHARE,
    CompanyLinks,
    Direction,
    LinkedCompany,
    by_revenue,
    fetch_links,
)
from app.profile.knowledge import ROLE_FACTS, new_fact, profile_with_knowledge, revise
from app.profile.legend import Legends, get_legends
from app.profile.okved import SEGMENTS, resolve
from app.profile.row import CompanyRow

TOP_PAYMENT_CARDS = 30
TOP_GROUP_CARDS = 20
CARRIER_SEGMENT = "logistics"
MANUAL_PREFIX = "manual:"
ROLE_WORDS = {
    CounterpartyRole.BUYER: "покупатель",
    CounterpartyRole.SUPPLIER: "поставщик",
    CounterpartyRole.CARRIER: "перевозчик",
}
ROLE_BY_DIRECTION = {"incoming": CounterpartyRole.BUYER, "outgoing": CounterpartyRole.SUPPLIER}
CONSEQUENCES = {
    CounterpartyRole.BUYER: "спад на его рынке ударит по вашей выручке",
    CounterpartyRole.SUPPLIER: "рост цен и перебои у него отразятся на ваших затратах",
    CounterpartyRole.CARRIER: "рост тарифов и перебои в перевозках отразятся на сроках и затратах",
}
ROLE_VALUES = {role.value for role in CounterpartyRole}
ORIGIN_ORDER = [LinkOrigin.PAYMENTS, LinkOrigin.GROUP, LinkOrigin.SHARED_PERSON]

SCHEMA = """
CREATE TABLE IF NOT EXISTS manual_links (
    link_id text PRIMARY KEY,
    company_id text NOT NULL,
    name text NOT NULL,
    role text NOT NULL,
    industry text,
    okved_code text,
    note text,
    deleted boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS manual_links_company ON manual_links (company_id);
"""
INSERT_SQL = """
INSERT INTO manual_links (link_id, company_id, name, role, industry, okved_code, note,
                          deleted, created_at)
VALUES ($1, $2, $3, $4, $5, $6, $7, false, $8)
"""
LIST_SQL = """
SELECT * FROM manual_links WHERE company_id = $1 AND NOT deleted ORDER BY created_at, link_id
"""
ROLE_SQL = """
UPDATE manual_links SET role = $3 WHERE company_id = $1 AND link_id = $2 AND NOT deleted
"""
DELETE_SQL = """
UPDATE manual_links SET deleted = true WHERE company_id = $1 AND link_id = $2 AND NOT deleted
"""


class LinkNotFoundError(Exception):
    pass


@dataclass(frozen=True)
class ManualLink:
    link_id: str
    company_id: str
    name: str
    role: CounterpartyRole
    industry: str | None = None
    okved_code: str | None = None
    note: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def segment(self) -> str | None:
        info = resolve(self.okved_code, self.industry)
        return None if info.segment.id == "other" else info.segment.id

    @property
    def industry_name(self) -> str | None:
        return self.industry or resolve(self.okved_code).class_name


@dataclass
class Draft:
    """Карточка в сборке: связь копится из нескольких источников, потом замораживается."""

    company: LinkedCompany
    origins: set[LinkOrigin] = field(default_factory=set)
    turnover: dict[Direction, Turnover] = field(default_factory=dict)

    @property
    def amount(self) -> float:
        return sum(item.amount_rub for item in self.turnover.values())


def role_key(partner_id: str) -> str:
    return f"counterparty.{partner_id}.role"


def known_roles(base: KnowledgeBase) -> dict[str, KnowledgeFact]:
    """Факты о типе связи по SYNTHETIC_U контрагента: действующие, клиента или легенды."""
    roles: dict[str, KnowledgeFact] = {}
    for fact in base.facts:
        key = fact.key or ""
        if key.startswith("counterparty.") and key.endswith(".role") and fact.value in ROLE_VALUES:
            roles[key.removeprefix("counterparty.").removesuffix(".role")] = fact
    return roles


def share(part: float, total: float) -> float:
    return min(1.0, part / total) if total > 0 else 0.0


def totals(links: CompanyLinks, direction: Direction) -> PaymentTotals:
    payments = [payment for payment in links.payments if payment.direction == direction]
    return PaymentTotals(
        amount_rub=float(sum((payment.amount_rub for payment in payments), Decimal(0))),
        payments_count=sum(payment.payments_count or 0 for payment in payments),
        counterparties=len({payment.partner.company_id for payment in payments}),
    )


def payment_role(draft: Draft) -> CounterpartyRole | None:
    directions = list(draft.turnover)
    return ROLE_BY_DIRECTION[directions[0]] if len(directions) == 1 else None


def bank_card(
    client_id: str, draft: Draft, roles: dict[str, KnowledgeFact], legends: Legends
) -> LinkedCompanyCard:
    company = draft.company
    legend = legends.partner(client_id, company.company_id, company.segment)
    fact = roles.get(company.company_id)
    role: CounterpartyRole | None = None
    source: RoleSource | None = None
    if fact is not None:
        role = CounterpartyRole(str(fact.value))
        source = RoleSource.CLIENT if fact.source is FactSource.CLIENT else RoleSource.LEGEND
    elif legend is not None and legend.role is not None:
        role, source = legend.role, RoleSource.LEGEND
    elif (guess := payment_role(draft)) is not None:
        role, source = guess, RoleSource.PAYMENTS
    segment = company.segment or (legend.segment if legend else None)
    return LinkedCompanyCard(
        id=company.company_id,
        company_id=company.company_id,
        name=legend.name if legend else None,
        name_source="legend" if legend else None,
        origins=[origin for origin in ORIGIN_ORDER if origin in draft.origins],
        role=role,
        role_source=source,
        role_fact_id=fact.id if fact is not None else None,
        incoming=draft.turnover.get("incoming"),
        outgoing=draft.turnover.get("outgoing"),
        industry=company.okved_name or (legend.industry if legend else None),
        segment=segment,
        region=company.region,
        revenue_rub=float(company.revenue_rub) if company.revenue_rub is not None else None,
    )


def manual_card(link: ManualLink) -> LinkedCompanyCard:
    return LinkedCompanyCard(
        id=link.link_id,
        name=link.name,
        name_source="client",
        origins=[LinkOrigin.CLIENT],
        role=link.role,
        role_source=RoleSource.CLIENT,
        industry=link.industry_name,
        segment=link.segment,
        note=link.note,
    )


def build_cards(
    client_id: str,
    links: CompanyLinks,
    base: KnowledgeBase,
    manual: list[ManualLink],
    legends: Legends,
) -> LinkedCompanies:
    """Карточки: крупнейшие по обороту контрагенты, все с известным типом связи, крупнейшие
    компании группы и связанные через общих физлиц, затем добавленные клиентом."""
    incoming, outgoing = totals(links, "incoming"), totals(links, "outgoing")
    flows = {"incoming": incoming, "outgoing": outgoing}
    drafts: dict[str, Draft] = {}

    def draft_of(company: LinkedCompany) -> Draft:
        return drafts.setdefault(company.company_id, Draft(company))

    for payment in links.payments:
        total = flows[payment.direction]
        draft = draft_of(payment.partner)
        draft.origins.add(LinkOrigin.PAYMENTS)
        count = payment.payments_count
        draft.turnover[payment.direction] = Turnover(
            amount_rub=float(payment.amount_rub),
            payments_count=count,
            amount_share=share(float(payment.amount_rub), total.amount_rub),
            count_share=share(count, total.payments_count) if count is not None else None,
        )
    roles = known_roles(base)
    ranked = sorted(drafts.values(), key=lambda draft: draft.amount, reverse=True)
    shown = {draft.company.company_id for draft in ranked[:TOP_PAYMENT_CARDS]}
    shown |= {
        draft.company.company_id
        for draft in ranked
        if draft.company.company_id in roles
        or legends.partner(client_id, draft.company.company_id, None) is not None
    }
    hidden = len(drafts) - len(shown)

    members = sorted(links.group_members, key=by_revenue, reverse=True)[:TOP_GROUP_CARDS]
    for origin, companies in (
        (LinkOrigin.GROUP, members),
        (LinkOrigin.SHARED_PERSON, links.shared_person_companies),
    ):
        for company in companies:
            draft_of(company).origins.add(origin)
            shown.add(company.company_id)

    def order(draft: Draft) -> tuple[float, int, Decimal]:
        first = min(ORIGIN_ORDER.index(origin) for origin in draft.origins)
        return -draft.amount, first, -(draft.company.revenue_rub or Decimal(0))

    cards = [
        bank_card(client_id, draft, roles, legends)
        for draft in sorted(drafts.values(), key=order)
        if draft.company.company_id in shown
    ]
    warnings = [] if links.payments else ["Платежей нет: доли в обороте не посчитаны"]
    return LinkedCompanies(
        company_id=client_id,
        cards=[*cards, *(manual_card(link) for link in manual)],
        incoming=incoming,
        outgoing=outgoing,
        hidden=hidden,
        warnings=warnings,
    )


def role_fact(
    base: KnowledgeBase,
    card: LinkedCompanyCard,
    role: CounterpartyRole,
    now: datetime | None = None,
) -> KnowledgeFact:
    """Тип связи от клиента. Если по контрагенту уже есть факт (например, из легенды), это его
    новая версия: у одного ключа одна история."""
    key = role_key(card.id)
    category, word = ROLE_FACTS[role]
    text = f"{card.name} ({card.id}) — {word}" if card.name else f"{card.id} — {word}"
    current = next((fact for fact in base.facts if fact.key == key), None)
    if current is not None:
        return revise(
            current, now, source=FactSource.CLIENT, category=category, text=text, value=role.value
        )
    return new_fact(
        base.company_id, category, text, FactSource.CLIENT, key=key, value=role.value, now=now
    )


def manual_link(
    company_id: str,
    name: str,
    role: CounterpartyRole,
    *,
    industry: str | None = None,
    okved_code: str | None = None,
    note: str | None = None,
    now: datetime | None = None,
) -> ManualLink:
    return ManualLink(
        link_id=f"{MANUAL_PREFIX}{uuid4().hex[:12]}",
        company_id=company_id,
        name=name.strip(),
        role=role,
        industry=(industry or "").strip() or None,
        okved_code=(okved_code or "").strip() or None,
        note=(note or "").strip() or None,
        created_at=now or datetime.now(UTC),
    )


def client_carriers(profile: CompanyProfile) -> dict[str, str]:
    """Контрагенты, которых клиент назвал перевозчиками: SYNTHETIC_U → признак с его ответом."""
    prefix, suffix = "feature:knowledge:counterparty.", ".role"
    return {
        feature.id.removeprefix(prefix).removesuffix(suffix): feature.id
        for feature in profile.features
        if feature.id.startswith(prefix)
        and feature.id.endswith(suffix)
        and feature.value == CounterpartyRole.CARRIER.value
    }


def with_carriers(profile: CompanyProfile) -> list[Exposure]:
    """Чувствительность к логистике от перевозчиков, которых отметил клиент. По платежам
    перевозчика не отличить от поставщика, поэтому без ответа клиента её нет. Мелкий перевозчик
    не в счёт: новости о тарифах нужны тем, у кого перевозки — заметная доля оборота."""
    carriers = client_carriers(profile)
    shares: dict[str, tuple[float, str]] = {}
    for item in profile.counterparties:
        share = item.share or 0.0
        if item.company_id in carriers and share >= float(SIGNIFICANT_SHARE):
            name = item.legend.name if item.legend else item.company_id
            if share > shares.get(item.company_id, (0.0, ""))[0]:
                shares[item.company_id] = share, f"{name} — {share * 100:.0f}%"
    if not shares:
        return list(profile.exposures)
    ranked = sorted(shares.items(), key=lambda pair: pair[1][0], reverse=True)
    parts = ", ".join(part for _, (_, part) in ranked)
    reason = (
        f"Со слов клиента, перевозчики получают заметную долю ваших платежей: {parts}. "
        f"{CONSEQUENCES[CounterpartyRole.CARRIER].capitalize()}"
    )
    feature_ids = [carriers[company_id] for company_id, _ in ranked]
    keywords = list(SEGMENTS[CARRIER_SEGMENT].keywords)
    exposures = list(profile.exposures)
    for index, current in enumerate(exposures):
        if current.topic is Topic.INDUSTRY and current.keywords == keywords:
            exposures[index] = current.model_copy(
                update={
                    "reason": f"{current.reason}. {reason}",
                    "feature_ids": list(dict.fromkeys([*current.feature_ids, *feature_ids])),
                }
            )
            return exposures
    exposure = Exposure(
        topic=Topic.INDUSTRY,
        direction=ExposureDirection.RISK,
        reason=reason,
        feature_ids=feature_ids,
        keywords=keywords,
    )
    return [*exposures, exposure]


def apply_links(profile: CompanyProfile, manual: list[ManualLink]) -> CompanyProfile:
    """Профиль с тем, что клиент сказал о связанных компаниях: перевозчики из платежей дают
    чувствительность к логистике, а добавленные вручную компании — признак и отраслевую
    чувствительность, если отрасль понятна. Отбор новостей найдёт рынок такой компании."""
    exposures = with_carriers(profile)
    if not manual and exposures == profile.exposures:
        return profile
    features: list[ProfileFeature] = []
    for link in manual:
        word = ROLE_WORDS[link.role]
        segment = link.segment
        where = f", отрасль «{SEGMENTS[segment].name}»" if segment else ""
        feature_id = f"feature:link:{link.link_id.removeprefix(MANUAL_PREFIX)}"
        features.append(
            ProfileFeature(
                id=feature_id,
                kind=FeatureKind.COUNTERPARTY,
                label=f"Со слов клиента: {link.name} — {word}{where}",
                value=link.role.value,
                source_fields=[f"links:{link.link_id}"],
            )
        )
        if segment:
            exposures.append(
                Exposure(
                    topic=Topic.INDUSTRY,
                    direction=ExposureDirection.MIXED,
                    reason=f"Ваш {word} {link.name} работает в отрасли "
                    f"«{SEGMENTS[segment].name}»: {CONSEQUENCES[link.role]}",
                    feature_ids=[feature_id],
                    keywords=list(SEGMENTS[segment].keywords),
                )
            )
    return profile.model_copy(
        update={
            "features": [*profile.features, *features],
            "exposures": exposures,
        }
    )


def from_row(record: Any) -> ManualLink:
    return ManualLink(
        link_id=record["link_id"],
        company_id=record["company_id"],
        name=record["name"],
        role=CounterpartyRole(record["role"]),
        industry=record["industry"],
        okved_code=record["okved_code"],
        note=record["note"],
        created_at=record["created_at"],
    )


async def ensure_schema(connection: Any) -> None:
    await connection.execute(SCHEMA)


async def manual_links(connection: Any, company_id: str) -> list[ManualLink]:
    return [from_row(record) for record in await connection.fetch(LIST_SQL, company_id)]


async def add_manual(connection: Any, link: ManualLink) -> ManualLink:
    await ensure_schema(connection)
    await connection.execute(
        INSERT_SQL,
        link.link_id,
        link.company_id,
        link.name,
        link.role.value,
        link.industry,
        link.okved_code,
        link.note,
        link.created_at,
    )
    return link


async def set_manual_role(
    connection: Any, company_id: str, link_id: str, role: CounterpartyRole
) -> None:
    await ensure_schema(connection)
    if await connection.execute(ROLE_SQL, company_id, link_id, role.value) == "UPDATE 0":
        raise LinkNotFoundError(link_id)


async def remove_manual(connection: Any, company_id: str, link_id: str) -> None:
    """Удаление помечает запись: что клиент добавлял, остаётся в базе."""
    await ensure_schema(connection)
    if await connection.execute(DELETE_SQL, company_id, link_id) == "UPDATE 0":
        raise LinkNotFoundError(link_id)


async def linked_companies(
    connection: Any, company_id: str, legends: Legends | None = None
) -> LinkedCompanies | None:
    """Карточки связанных компаний с актуальной базой знаний. None — компании нет."""
    loaded = await profile_with_knowledge(connection, company_id)
    if loaded is None:
        return None
    record = await connection.fetchrow(COMPANY_SQL, company_id)
    links = await fetch_links(connection, CompanyRow.from_record(record))
    await ensure_schema(connection)
    manual = await manual_links(connection, company_id)
    return build_cards(
        company_id, links, loaded[1], manual, get_legends() if legends is None else legends
    )


def find_card(cards: LinkedCompanies, card_id: str) -> LinkedCompanyCard:
    card = next((card for card in cards.cards if card.id == card_id), None)
    if card is None:
        raise LinkNotFoundError(card_id)
    return card
