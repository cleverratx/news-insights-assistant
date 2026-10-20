"""База знаний компании: факты о рынках, продукции, сезонности, ограничениях и прочем, что не
следует из выгрузки напрямую. Пишут её система, данные банка и сам клиент.

Хранение — история версий: правка или удаление добавляет новую версию факта, старые остаются.
Действующая база знаний собирается из последних версий; из фактов с одним ключом остаётся
факт с более приоритетным источником: клиент главнее данных банка, данные банка — системы.
"""

import json
from collections.abc import Iterable
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
) -> KnowledgeFact:
    """Первая версия факта. Факт клиента сразу подтверждён, автоматический — предположение."""
    return KnowledgeFact(
        id=f"fact:{uuid4().hex}",
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


def resolve(company_id: str, versions: Iterable[KnowledgeFact]) -> KnowledgeBase:
    """Действующая база знаний из истории версий."""
    latest: dict[str, KnowledgeFact] = {}
    for fact in versions:
        if fact.id not in latest or fact.version > latest[fact.id].version:
            latest[fact.id] = fact
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
