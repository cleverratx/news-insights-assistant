"""База знаний компании: факты о рынках, продукции, сезонности, ограничениях и прочем, что не
следует из выгрузки напрямую. Пишут её система, данные банка и сам клиент.

Хранение — история версий: правка или удаление добавляет новую версию факта, старые остаются.
Действующая база знаний собирается из последних версий; из фактов с одним ключом остаётся
факт с более приоритетным источником: клиент главнее данных банка, данные банка — системы.
"""

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.contracts.knowledge import (
    SOURCE_PRIORITY,
    FactCategory,
    FactSource,
    FactStatus,
    KnowledgeBase,
    KnowledgeFact,
)
from app.contracts.news import Topic
from app.contracts.profile import (
    CompanyProfile,
    CounterpartyRole,
    Exposure,
    FeatureKind,
    ProfileFeature,
)
from app.profile.builder import load_profile
from app.profile.derived import FX_EXPOSURES
from app.profile.okved import SEGMENTS

SCHEMA = """
CREATE TABLE IF NOT EXISTS knowledge_facts (
    fact_id text NOT NULL,
    version integer NOT NULL,
    company_id text NOT NULL,
    key text,
    category text NOT NULL,
    text text NOT NULL,
    value jsonb,
    source text NOT NULL,
    status text NOT NULL,
    evidence text[] NOT NULL DEFAULT '{}',
    deleted boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL,
    PRIMARY KEY (fact_id, version)
);
CREATE INDEX IF NOT EXISTS knowledge_facts_company ON knowledge_facts (company_id);
"""
INSERT_SQL = """
INSERT INTO knowledge_facts (fact_id, version, company_id, key, category, text, value, source,
                             status, evidence, deleted, created_at)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
"""
HISTORY_SQL = "SELECT * FROM knowledge_facts WHERE company_id = $1 ORDER BY fact_id, version"
LATEST_SQL = "SELECT * FROM knowledge_facts WHERE fact_id = $1 ORDER BY version DESC LIMIT 1"


class FactNotFoundError(Exception):
    pass


def new_fact(
    company_id: str,
    category: FactCategory,
    text: str,
    source: FactSource,
    *,
    key: str | None = None,
    value: str | float | bool | None = None,
    evidence: list[str] | None = None,
    now: datetime | None = None,
    fact_id: str | None = None,
) -> KnowledgeFact:
    """Первая версия факта. Факт клиента сразу подтверждён, автоматический — предположение."""
    return KnowledgeFact(
        id=fact_id or f"fact:{uuid4().hex}",
        company_id=company_id,
        version=1,
        key=key,
        category=category,
        text=text,
        value=value,
        source=source,
        status=FactStatus.CONFIRMED if source is FactSource.CLIENT else FactStatus.ASSUMPTION,
        evidence=evidence or [],
        created_at=now or datetime.now(UTC),
    )


def revise(fact: KnowledgeFact, now: datetime | None = None, **changes: Any) -> KnowledgeFact:
    """Следующая версия факта с правками. Правка клиента делает факт клиентским и подтверждённым."""
    data = fact.model_dump() | changes
    if FactSource(data["source"]) is FactSource.CLIENT:
        data["status"] = FactStatus.CONFIRMED
    data["version"] = fact.version + 1
    data["created_at"] = now or datetime.now(UTC)
    return KnowledgeFact.model_validate(data)


def confirm(fact: KnowledgeFact, now: datetime | None = None) -> KnowledgeFact:
    """Клиент подтвердил автоматический факт: он становится фактом клиента."""
    return revise(fact, now, source=FactSource.CLIENT, status=FactStatus.CONFIRMED)


def delete(fact: KnowledgeFact, now: datetime | None = None) -> KnowledgeFact:
    return revise(fact, now, deleted=True)


def rank(fact: KnowledgeFact) -> tuple[int, int, float]:
    confirmed = 0 if fact.status is FactStatus.CONFIRMED else 1
    return SOURCE_PRIORITY[fact.source], confirmed, -fact.created_at.timestamp()


def latest_versions(versions: Iterable[KnowledgeFact]) -> dict[str, KnowledgeFact]:
    latest: dict[str, KnowledgeFact] = {}
    for fact in versions:
        if fact.id not in latest or fact.version > latest[fact.id].version:
            latest[fact.id] = fact
    return latest


def resolve(company_id: str, versions: Iterable[KnowledgeFact]) -> KnowledgeBase:
    """Действующая база знаний из истории версий."""
    latest = latest_versions(versions)
    alive = [fact for fact in latest.values() if not fact.deleted]
    by_key: dict[str, list[KnowledgeFact]] = {}
    facts: list[KnowledgeFact] = []
    for fact in alive:
        if fact.key is None:
            facts.append(fact)
        else:
            by_key.setdefault(fact.key, []).append(fact)
    superseded: list[KnowledgeFact] = []
    for candidates in by_key.values():
        winner, *losers = sorted(candidates, key=rank)
        facts.append(winner)
        superseded.extend(losers)
    order = {fact.id: index for index, fact in enumerate(latest.values())}
    return KnowledgeBase(
        company_id=company_id,
        facts=sorted(facts, key=lambda fact: order[fact.id]),
        superseded=sorted(superseded, key=lambda fact: order[fact.id]),
    )


def as_row(fact: KnowledgeFact) -> tuple[object, ...]:
    return (
        fact.id,
        fact.version,
        fact.company_id,
        fact.key,
        fact.category.value,
        fact.text,
        json.dumps(fact.value, ensure_ascii=False) if fact.value is not None else None,
        fact.source.value,
        fact.status.value,
        fact.evidence,
        fact.deleted,
        fact.created_at,
    )


def from_row(record: Any) -> KnowledgeFact:
    return KnowledgeFact(
        id=record["fact_id"],
        company_id=record["company_id"],
        version=record["version"],
        key=record["key"],
        category=record["category"],
        text=record["text"],
        value=json.loads(record["value"]) if record["value"] is not None else None,
        source=record["source"],
        status=record["status"],
        evidence=list(record["evidence"]),
        deleted=record["deleted"],
        created_at=record["created_at"],
    )


async def ensure_schema(connection: Any) -> None:
    await connection.execute(SCHEMA)


async def save(connection: Any, fact: KnowledgeFact) -> KnowledgeFact:
    """Записывает версию. Номер версии должен быть следующим после последней сохранённой."""
    async with connection.transaction():
        record = await connection.fetchrow(LATEST_SQL, fact.id)
        expected = record["version"] + 1 if record is not None else 1
        if fact.version != expected:
            raise ValueError(f"Ожидалась версия {expected} факта {fact.id}, пришла {fact.version}")
        await connection.execute(INSERT_SQL, *as_row(fact))
    return fact


async def latest_version(connection: Any, fact_id: str) -> KnowledgeFact:
    record = await connection.fetchrow(LATEST_SQL, fact_id)
    if record is None:
        raise FactNotFoundError(fact_id)
    return from_row(record)


async def history(connection: Any, company_id: str) -> list[KnowledgeFact]:
    return [from_row(record) for record in await connection.fetch(HISTORY_SQL, company_id)]


async def knowledge_base(connection: Any, company_id: str) -> KnowledgeBase:
    return resolve(company_id, await history(connection, company_id))


# Черновик из данных банка (A2-13): что видно по выгрузке, записано как предположение, пока
# клиент не подтвердит. Взятое из выгрузки как есть — источник bank_data, наши оценки поверх
# неё — system. У фактов черновика постоянные id и ключи: повторная сборка обновляет их,
# а не плодит копии, и не трогает то, что клиент уже подтвердил, исправил или удалил.

DRAFT_PREFIX = "draft"
TOP_PARTNER_SEGMENTS = 3
PARTNER_ROLES = {"incoming": "покупатели", "outgoing": "поставщики"}
DRAFT_CHANGES = {"category", "text", "value", "source", "evidence"}
ROLE_FACTS = {
    CounterpartyRole.BUYER: (FactCategory.CUSTOMERS, "покупатель"),
    CounterpartyRole.SUPPLIER: (FactCategory.SUPPLIERS, "поставщик"),
    CounterpartyRole.CARRIER: (FactCategory.SUPPLIERS, "перевозчик"),
}


@dataclass(frozen=True)
class DraftSync:
    created: int = 0
    updated: int = 0
    removed: int = 0
    kept: int = 0


def draft_id(company_id: str, key: str) -> str:
    return f"{DRAFT_PREFIX}:{company_id}:{key}"


def partner_summary(profile: CompanyProfile, direction: str) -> str | None:
    shares: dict[str, float] = {}
    for item in profile.counterparties:
        if item.direction == direction:
            segment = SEGMENTS[item.segment].name if item.segment else "отрасль не известна"
            shares[segment] = shares.get(segment, 0.0) + (item.share or 0.0)
    if not shares:
        return None
    top = sorted(shares.items(), key=lambda pair: pair[1], reverse=True)[:TOP_PARTNER_SEGMENTS]
    parts = ", ".join(f"{segment} — {share * 100:.0f}%" for segment, share in top)
    return f"Крупнейшие {PARTNER_ROLES[direction]} по платежам: {parts} оборота"


def draft(profile: CompanyProfile, now: datetime | None = None) -> list[KnowledgeFact]:
    """Черновик базы знаний по профилю. Каждый факт ссылается на признаки, из которых собран."""
    features = {feature.id: feature for feature in profile.features}
    facts: list[KnowledgeFact] = []

    def add(
        key: str,
        category: FactCategory,
        source: FactSource,
        ids: list[str],
        text: str | None = None,
        value: str | float | bool | None = None,
    ) -> None:
        found = [features[ref] for ref in ids if ref in features]
        if not found:
            return
        facts.append(
            new_fact(
                profile.company_id,
                category,
                text or "; ".join(feature.label for feature in found),
                source,
                key=key,
                value=value,
                evidence=[feature.id for feature in found],
                now=now,
                fact_id=draft_id(profile.company_id, key),
            )
        )

    industry = profile.industry
    add(
        "products.industry",
        FactCategory.PRODUCTS,
        FactSource.BANK_DATA if industry.resolved_by == "code" else FactSource.SYSTEM,
        ["feature:industry"],
        f"Основная деятельность: {industry.okved_name or industry.segment_name}"
        f" (сегмент «{industry.segment_name}»)",
        industry.segment,
    )
    size = profile.scale.size_category
    add(
        "company.size",
        FactCategory.OTHER,
        FactSource.BANK_DATA,
        ["feature:revenue", "feature:size", "feature:employees"],
        value=size.value if size else None,
    )
    products = [f"feature:{product.value}" for product in profile.products]
    add("bank.products", FactCategory.OTHER, FactSource.BANK_DATA, products)
    add(
        "constraints.debt", FactCategory.CONSTRAINTS, FactSource.SYSTEM, ["feature:debt_dependence"]
    )
    add(
        "markets.foreign_trade",
        FactCategory.MARKETS,
        FactSource.BANK_DATA,
        ["feature:customs"],
        value=", ".join(profile.foreign_trade.countries) or None,
    )
    direction = features.get("feature:trade_direction")
    add(
        "markets.trade_direction",
        FactCategory.MARKETS,
        FactSource.SYSTEM,
        ["feature:trade_direction"],
        value=direction.value if direction else None,
    )
    add(
        "constraints.sanctions",
        FactCategory.CONSTRAINTS,
        FactSource.SYSTEM,
        ["feature:unfriendly_trade", "feature:payment_hub_trade"],
    )
    add(
        "markets.regions",
        FactCategory.MARKETS,
        FactSource.BANK_DATA,
        ["feature:region", "feature:city", "feature:geography"],
    )
    for side, key, category, concentration in (
        ("incoming", "customers.main", FactCategory.CUSTOMERS, "feature:customer_concentration"),
        ("outgoing", "suppliers.main", FactCategory.SUPPLIERS, "feature:supplier_concentration"),
    ):
        summary = partner_summary(profile, side)
        if summary is not None:
            ids = [f"feature:counterparties_{side}", concentration]
            add(key, category, FactSource.BANK_DATA, ids, summary)
    add(
        "company.group",
        FactCategory.OTHER,
        FactSource.BANK_DATA,
        ["feature:group", "feature:group_segments"],
    )
    return facts + legend_roles(profile, now)


def legend_roles(profile: CompanyProfile, now: datetime | None = None) -> list[KnowledgeFact]:
    """Роли контрагентов из демо-легенды — предположения с источником legend: клиент
    подтверждает их или исправляет, и его ответ главнее легенды."""
    facts: dict[str, KnowledgeFact] = {}
    for item in profile.counterparties:
        legend = item.legend
        if legend is None or legend.role is None:
            continue
        key = f"counterparty.{item.company_id}.role"
        category, word = ROLE_FACTS[legend.role]
        facts.setdefault(
            key,
            new_fact(
                profile.company_id,
                category,
                f"{legend.name} ({item.company_id}) — {word}",
                FactSource.LEGEND,
                key=key,
                value=legend.role.value,
                evidence=[f"feature:counterparties_{item.direction}", f"legend:{item.company_id}"],
                now=now,
                fact_id=draft_id(profile.company_id, key),
            ),
        )
    return list(facts.values())


def same_content(left: KnowledgeFact, right: KnowledgeFact) -> bool:
    return left.model_dump(include=DRAFT_CHANGES) == right.model_dump(include=DRAFT_CHANGES)


async def sync_draft(
    connection: Any, profile: CompanyProfile, now: datetime | None = None
) -> DraftSync:
    """Приводит черновик в базе к текущему профилю. Факты, которые клиент подтвердил, исправил
    или удалил, не трогаем: решение клиента главнее данных."""
    moment = now or datetime.now(UTC)
    fresh = {fact.id: fact for fact in draft(profile, moment)}
    latest = latest_versions(await history(connection, profile.company_id))
    created = updated = removed = kept = 0
    for fact_id, fact in fresh.items():
        current = latest.get(fact_id)
        if current is None:
            await save(connection, fact)
            created += 1
        elif current.deleted or current.source is FactSource.CLIENT or same_content(current, fact):
            kept += 1
        else:
            await save(
                connection, revise(current, moment, **fact.model_dump(include=DRAFT_CHANGES))
            )
            updated += 1
    prefix = f"{DRAFT_PREFIX}:{profile.company_id}:"
    for fact_id, current in latest.items():
        stale = fact_id.startswith(prefix) and fact_id not in fresh
        if stale and not current.deleted and current.source is not FactSource.CLIENT:
            await save(connection, delete(current, moment))
            removed += 1
    return DraftSync(created=created, updated=updated, removed=removed, kept=kept)


async def knowledge_for(connection: Any, company_id: str) -> KnowledgeBase | None:
    """База знаний компании с актуальным черновиком из данных банка. None — компании нет."""
    loaded = await profile_with_knowledge(connection, company_id)
    return loaded[1] if loaded else None


async def profile_with_knowledge(
    connection: Any, company_id: str
) -> tuple[CompanyProfile, KnowledgeBase] | None:
    profile = await load_profile(connection, company_id)
    if profile is None:
        return None
    await ensure_schema(connection)
    await sync_draft(connection, profile)
    return profile, await knowledge_base(connection, company_id)


# Правки клиента в профиле (A2-14): факты клиента становятся признаками профиля, а то, что
# клиент знает лучше данных, меняет чувствительность. Профиль собирается на каждый запрос,
# поэтому правка сразу видна отбору и агенту, которые читают профиль.

TRADE_DIRECTION_KEY = "markets.trade_direction"
COMPETITORS_PREFIX = "competitors."


def knowledge_feature_id(fact: KnowledgeFact) -> str:
    return f"feature:knowledge:{fact.key}" if fact.key else f"feature:{fact.id}"


def apply_knowledge(profile: CompanyProfile, base: KnowledgeBase) -> CompanyProfile:
    """Профиль с учётом подтверждённого клиентом. Автоматические факты уже есть в профиле,
    а конкурентов в профиль приносит app/profile/competitors.py."""
    client_facts = [
        fact
        for fact in base.facts
        if fact.source is FactSource.CLIENT and not (fact.key or "").startswith(COMPETITORS_PREFIX)
    ]
    if not client_facts:
        return profile
    features = [
        ProfileFeature(
            id=knowledge_feature_id(fact),
            kind=FeatureKind.KNOWLEDGE,
            label=f"Со слов клиента: {fact.text}",
            value=fact.value,
            source_fields=[f"knowledge:{fact.id}"],
        )
        for fact in client_facts
    ]
    exposures = list(profile.exposures)
    direction = next((fact for fact in client_facts if fact.key == TRADE_DIRECTION_KEY), None)
    if direction is not None and direction.value in FX_EXPOSURES:
        fx = next((item for item in exposures if item.topic is Topic.FX), None)
        sign, reason = FX_EXPOSURES[str(direction.value)]
        customs = [ref for ref in (fx.feature_ids if fx else []) if ref == "feature:customs"]
        updated = Exposure(
            topic=Topic.FX,
            direction=sign,
            reason=f"{reason}. Направление ВЭД подтвердил клиент: {direction.text}",
            feature_ids=[knowledge_feature_id(direction), *customs],
            keywords=fx.keywords if fx else ["курс рубля"],
        )
        exposures = [updated if item is fx else item for item in exposures]
        if fx is None:
            exposures.append(updated)
    return profile.model_copy(
        update={"features": [*profile.features, *features], "exposures": exposures}
    )
