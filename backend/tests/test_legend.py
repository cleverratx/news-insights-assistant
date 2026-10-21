import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import asyncpg
import pytest
import yaml

from app.contracts.knowledge import SOURCE_PRIORITY, FactCategory, FactSource, FactStatus
from app.contracts.profile import CompanyProfile, CounterpartyRole
from app.core.config import get_settings
from app.data.loader import read_case, store
from app.profile.builder import assemble, load_profile
from app.profile.graph import CompanyLinks, LinkedCompany, Payment
from app.profile.knowledge import draft, new_fact, resolve
from app.profile.legend import (
    LegendFileError,
    Legends,
    apply_legend,
    get_legends,
    load_legends,
    parse_legends,
)
from app.profile.row import CompanyRow
from tests.test_data_loader import database
from tests.test_samples import COMPANIES, LINKS, SAMPLES

__all__ = ["database"]

T0 = datetime(2026, 10, 21, 9, tzinfo=UTC)
LEGEND = """
companies:
  - synthetic_u: U_1
    name: АО «Тестовый Улов»
    description: Оптовик рыбой
    brands: [Улов]
    industry_hint: лишнее поле заготовки
    partners:
      - synthetic_u: U_10
        name: ООО «Магазин»
        role: покупатель
      - synthetic_u: U_20
        name: ООО «Химзавод»
        role: Поставщик
        industry: Производство прочих основных неорганических химических веществ
        okved_code: "20.13"
      - synthetic_u: U_30
        name: ""
        role: перевозчик
      - synthetic_u: U_40
        name: ООО «Связанная»
  - synthetic_u: U_2
    name: ""
"""
CLIENT = CompanyRow(
    company_id="U_1",
    okved_code="46.38",
    okved_name="Торговля оптовая рыбой",
    revenue_rub=Decimal("18200000000"),
    region="Москва",
    has_loan=True,
    group_id="g_1",
)
LINKS_OF_CLIENT = CompanyLinks(
    group_members=[LinkedCompany("U_40", "52.10")],
    payments=[
        Payment(LinkedCompany("U_10", "47.11"), "incoming", Decimal("600"), 6),
        Payment(LinkedCompany("U_20"), "outgoing", Decimal("400"), 4),
        Payment(LinkedCompany("U_30", "49.41"), "outgoing", Decimal("100"), 1),
    ],
)


def profile() -> CompanyProfile:
    return assemble(CLIENT, LINKS_OF_CLIENT)


def without_legend(data: Any) -> Any:
    """Профиль без полей легенды: всё остальное легенда менять не должна."""
    if isinstance(data, dict):
        return {key: without_legend(value) for key, value in data.items() if key != "legend"}
    if isinstance(data, list):
        return [without_legend(item) for item in data]
    return data


def test_parse_reads_roles_in_words_and_skips_empty_names() -> None:
    legends = parse_legends(LEGEND)
    assert set(legends.companies) == {"U_1"}
    assert legends.companies["U_1"].brands == ["Улов"]
    assert legends.name("U_10") == "ООО «Магазин»"
    assert legends.name("U_30") is None
    assert legends.roles[("U_1", "U_10")] is CounterpartyRole.BUYER
    assert legends.roles[("U_1", "U_20")] is CounterpartyRole.SUPPLIER
    assert legends.partners["U_20"].segment == "chemicals"


def test_broken_file_is_an_error_but_service_goes_on(tmp_path: Path) -> None:
    with pytest.raises(LegendFileError):
        parse_legends("companies: [")
    broken = tmp_path / "legends.yaml"
    broken.write_text("companies:\n  - name: без идентификатора\n", encoding="utf-8")
    assert not load_legends(broken)
    assert not load_legends(tmp_path / "нет такого.yaml")
    assert not parse_legends("")


def test_edited_file_is_picked_up_without_restart(tmp_path: Path) -> None:
    file = tmp_path / "legends.yaml"
    file.write_text(LEGEND, encoding="utf-8")
    assert load_legends(file).name("U_1") == "АО «Тестовый Улов»"
    file.write_text(LEGEND.replace("Тестовый Улов", "Другой Улов"), encoding="utf-8")
    stat = file.stat()
    os.utime(file, (stat.st_atime, stat.st_mtime + 10))
    assert load_legends(file).name("U_1") == "АО «Другой Улов»"


def test_legend_names_partners_and_keeps_data_as_is() -> None:
    plain = profile()
    named = apply_legend(plain, parse_legends(LEGEND))

    assert named.legend is not None and named.legend.name == "АО «Тестовый Улов»"
    partners = {item.company_id: item.legend for item in named.counterparties}
    assert partners["U_10"] is not None and partners["U_10"].role is CounterpartyRole.BUYER
    assert partners["U_10"].industry is None, "отрасль U_10 есть в выгрузке — правы данные"
    assert partners["U_20"] is not None and partners["U_20"].segment == "chemicals"
    assert partners["U_30"] is None
    assert named.group is not None and named.group.members[0].legend is not None
    assert named.group.members[0].legend.role is None

    assert without_legend(named.model_dump()) == without_legend(plain.model_dump())
    assert CompanyProfile.model_validate_json(named.model_dump_json()) == named


def test_role_belongs_to_the_pair_not_to_the_partner() -> None:
    legends = parse_legends(LEGEND)
    other = assemble(
        CompanyRow(company_id="U_5", okved_code="62.01"),
        CompanyLinks(payments=[Payment(LinkedCompany("U_10"), "outgoing", Decimal("1"), 1)]),
    )
    partner = apply_legend(other, legends).counterparties[0].legend
    assert partner is not None and partner.name == "ООО «Магазин»"
    assert partner.role is None


def test_without_legend_profile_is_on_identifiers() -> None:
    plain = profile()
    assert apply_legend(plain, Legends()) is plain
    assert plain.legend is None
    assert all(item.legend is None for item in plain.counterparties)


def test_legend_roles_go_to_knowledge_below_everyone() -> None:
    named = apply_legend(profile(), parse_legends(LEGEND))
    facts = {fact.key: fact for fact in draft(named, T0)}

    buyer = facts["counterparty.U_10.role"]
    assert buyer.source is FactSource.LEGEND
    assert buyer.status is FactStatus.ASSUMPTION
    assert buyer.category is FactCategory.CUSTOMERS
    assert buyer.value == "buyer"
    assert buyer.text == "ООО «Магазин» (U_10) — покупатель"
    assert buyer.evidence == ["feature:counterparties_incoming", "legend:U_10"]
    assert facts["counterparty.U_20.role"].category is FactCategory.SUPPLIERS
    assert "counterparty.U_30.role" not in facts
    assert max(SOURCE_PRIORITY, key=SOURCE_PRIORITY.__getitem__) is FactSource.LEGEND

    answer = new_fact(
        "U_1",
        FactCategory.SUPPLIERS,
        "U_10 — перевозчик",
        FactSource.CLIENT,
        key="counterparty.U_10.role",
        value="carrier",
    )
    base = resolve("U_1", [*facts.values(), answer])
    winner = next(fact for fact in base.facts if fact.key == "counterparty.U_10.role")
    assert winner.source is FactSource.CLIENT
    assert [fact.source for fact in base.superseded] == [FactSource.LEGEND]


def test_no_legend_no_legend_facts() -> None:
    assert not any(fact.source is FactSource.LEGEND for fact in draft(profile(), T0))


def test_committed_legend_is_complete() -> None:
    """Файл легенды в репозитории: у всех компаний и контрагентов есть названия, роли
    распознаны, а отрасль контрагента из легенды раскладывается в сегмент для поиска."""
    file = SAMPLES / "legends.yaml"
    legends = parse_legends(file.read_text(encoding="utf-8"))
    assert {"U_185947", "U_19717", "U_3263", "U_900001", "U_900002", "U_900003"} <= set(
        legends.companies
    )
    raw = yaml.safe_load(file.read_text(encoding="utf-8"))
    entries = [*raw["companies"], *(p for item in raw["companies"] for p in item["partners"])]
    assert all(entry["name"].strip() for entry in entries), "у каждой записи должно быть название"
    assert all(
        legends.roles.get((item["synthetic_u"], p["synthetic_u"])) is not None
        for item in raw["companies"]
        for p in item["partners"]
        if "role" in p
    ), "роль не распознана"
    for partner_id, partner in legends.partners.items():
        if partner.industry is not None:
            assert partner.segment is not None, f"отрасль {partner_id} не распознана"
    names = [item.name for item in legends.companies.values()]
    names += [item.name for item in legends.partners.values()]
    assert len(names) == len(set(names)), "условные названия не должны повторяться"


async def test_profile_loads_with_and_without_legend_file(
    database: asyncpg.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await store(database, read_case(COMPANIES, LINKS))

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    try:
        assert not get_legends()
        plain = await load_profile(database, "U_900001")
        monkeypatch.setenv("DATA_DIR", str(SAMPLES.parent))
        get_settings.cache_clear()
        named = await load_profile(database, "U_900001")
    finally:
        get_settings.cache_clear()

    assert plain is not None and plain.legend is None
    assert named is not None and named.legend is not None
    assert named.legend.name == "АО «Сытный Меридиан»"
    roles = {item.company_id: item.legend.role for item in named.counterparties if item.legend}
    assert roles["U_900102"] is CounterpartyRole.CARRIER
    assert without_legend(named.model_dump()) == without_legend(plain.model_dump())
