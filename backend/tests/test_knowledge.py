from datetime import UTC, datetime, timedelta

import asyncpg
import pytest
from pydantic import ValidationError

from app.contracts.knowledge import (
    FactCategory,
    FactSource,
    FactStatus,
    KnowledgeBase,
    KnowledgeFact,
)
from app.profile.knowledge import (
    FactNotFoundError,
    confirm,
    delete,
    ensure_schema,
    history,
    knowledge_base,
    latest_version,
    new_fact,
    resolve,
    revise,
    save,
)
from tests.test_data_loader import database

__all__ = ["database"]

T0 = datetime(2026, 10, 20, 9, tzinfo=UTC)


def auto(
    key: str | None = "markets.import_share", source: FactSource = FactSource.SYSTEM
) -> KnowledgeFact:
    return new_fact(
        "U_1",
        FactCategory.MARKETS,
        "Вероятно импортёр",
        source,
        key=key,
        value="import",
        evidence=["feature:trade_direction"],
        now=T0,
    )


def client(key: str | None = "markets.import_share", minutes: int = 0) -> KnowledgeFact:
    return new_fact(
        "U_1",
        FactCategory.MARKETS,
        "70% закупок — импорт из Китая",
        FactSource.CLIENT,
        key=key,
        value=0.7,
        now=T0 + timedelta(minutes=minutes),
    )


def test_new_facts_get_status_by_source() -> None:
    assert auto().status is FactStatus.ASSUMPTION
    assert client().status is FactStatus.CONFIRMED
    assert auto().version == 1


def test_client_fact_cannot_be_an_assumption() -> None:
    fact = client()
    with pytest.raises(ValidationError, match="всегда подтверждён"):
        fact.model_validate(fact.model_dump() | {"status": "assumption"})


def test_automatic_fact_needs_evidence() -> None:
    with pytest.raises(ValidationError, match="evidence"):
        new_fact("U_1", FactCategory.PRODUCTS, "Делает рыбные консервы", FactSource.SYSTEM)


def test_client_fact_wins_over_bank_and_system() -> None:
    system, bank, mine = auto(), auto(source=FactSource.BANK_DATA), client(minutes=-60)
    base = resolve("U_1", [system, bank, mine])
    assert [fact.id for fact in base.facts] == [mine.id]
    assert {fact.id for fact in base.superseded} == {system.id, bank.id}


def test_bank_data_wins_over_system() -> None:
    system, bank = auto(), auto(source=FactSource.BANK_DATA)
    base = resolve("U_1", [system, bank])
    assert base.facts[0].source is FactSource.BANK_DATA


def test_same_source_newest_wins() -> None:
    old, new = client(minutes=0), client(minutes=5)
    base = resolve("U_1", [old, new])
    assert base.facts == [new]
    assert base.superseded == [old]


def test_facts_without_key_never_compete() -> None:
    base = resolve("U_1", [auto(key=None), auto(key=None), client(key=None)])
    assert len(base.facts) == 3
    assert base.superseded == []


def test_latest_version_is_used_and_history_kept() -> None:
    first = auto()
    second = revise(first, T0 + timedelta(hours=1), text="Импортёр из Китая и Турции")
    assert second.version == 2
    assert second.id == first.id
    assert second.status is FactStatus.ASSUMPTION
    base = resolve("U_1", [second, first])
    assert base.facts == [second]


def test_client_edit_confirms_automatic_fact() -> None:
    edited = revise(auto(), T0, source=FactSource.CLIENT, value=0.6)
    assert edited.status is FactStatus.CONFIRMED
    assert edited.version == 2
    confirmed = confirm(auto(), T0)
    assert (confirmed.source, confirmed.status) == (FactSource.CLIENT, FactStatus.CONFIRMED)


def test_deleted_client_fact_lets_automatic_one_back() -> None:
    system, mine = auto(), client()
    removed = delete(mine, T0 + timedelta(hours=1))
    base = resolve("U_1", [system, mine, removed])
    assert base.facts == [system]
    assert base.superseded == []


def test_knowledge_base_rejects_foreign_or_deleted_facts() -> None:
    fact = client()
    with pytest.raises(ValidationError, match="другой компании"):
        KnowledgeBase(company_id="U_2", facts=[fact])
    with pytest.raises(ValidationError, match="Удалённый"):
        KnowledgeBase(company_id="U_1", facts=[delete(fact, T0)])


async def test_versions_stored_and_resolved_in_postgres(database: asyncpg.Connection) -> None:
    await ensure_schema(database)
    await ensure_schema(database)
    system = await save(database, auto())
    mine = await save(database, client())
    edited = await save(database, revise(mine, T0 + timedelta(hours=1), value=0.8))
    note = await save(
        database,
        new_fact("U_1", FactCategory.SEASONALITY, "Пик — декабрь", FactSource.CLIENT, now=T0),
    )

    assert len(await history(database, "U_1")) == 4
    assert (await latest_version(database, mine.id)).value == 0.8
    base = await knowledge_base(database, "U_1")
    assert {fact.id for fact in base.facts} == {edited.id, note.id}
    assert [fact.id for fact in base.superseded] == [system.id]
    assert next(fact for fact in base.facts if fact.id == edited.id).version == 2


async def test_version_gaps_and_repeats_rejected(database: asyncpg.Connection) -> None:
    await ensure_schema(database)
    fact = await save(database, client())
    with pytest.raises(ValueError, match="Ожидалась версия 2"):
        await save(database, fact)
    with pytest.raises(ValueError, match="Ожидалась версия 1"):
        await save(database, revise(client(), T0))
    with pytest.raises(FactNotFoundError):
        await latest_version(database, "fact:nope")
