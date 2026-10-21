from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.contracts.knowledge import FactCategory, FactSource, KnowledgeBase, KnowledgeFact
from app.contracts.links import LinkedCompanyCard, LinkOrigin, RoleSource, Turnover
from app.contracts.news import Topic
from app.contracts.profile import (
    CompanyProfile,
    CounterpartyRole,
    Exposure,
    ExposureDirection,
)
from app.profile.builder import assemble
from app.profile.graph import CompanyLinks, LinkedCompany, Payment
from app.profile.knowledge import apply_knowledge, new_fact, resolve
from app.profile.legend import Legends, parse_legends
from app.profile.links import (
    TOP_PAYMENT_CARDS,
    apply_links,
    build_cards,
    manual_card,
    manual_link,
    role_fact,
    role_key,
)
from app.profile.row import CompanyRow

NOW = datetime(2026, 10, 21, tzinfo=UTC)
CLIENT = CompanyRow(
    company_id="U_1", okved_code="46.38", okved_name="Оптовая торговля рыбой", group_id="g_1"
)
LEGENDS = parse_legends(
    """
companies:
  - synthetic_u: U_1
    name: АО «Рыбный двор»
    partners:
      - synthetic_u: U_4
        name: ООО «Полярный рейс»
        role: перевозчик
"""
)


def company(company_id: str, code: str | None = None, revenue: str | None = None) -> LinkedCompany:
    return LinkedCompany(
        company_id=company_id,
        okved_code=code,
        region="Москва",
        revenue_rub=Decimal(revenue) if revenue else None,
    )


def pay(partner: LinkedCompany, direction: str, amount: str, count: int | None) -> Payment:
    return Payment(
        partner=partner,
        direction="incoming" if direction == "in" else "outgoing",
        amount_rub=Decimal(amount),
        payments_count=count,
    )


LINKS = CompanyLinks(
    group_members=[company("U_2", "52.10", "900"), company("U_6", "10.20", "100")],
    payments=[
        pay(company("U_2", "52.10", "900"), "out", "100", 4),
        pay(company("U_3", "56.10"), "in", "750", 30),
        pay(company("U_5"), "in", "250", 10),
        pay(company("U_3", "56.10"), "out", "50", 1),
        pay(company("U_4", "49.41"), "out", "850", 15),
    ],
    shared_person_companies=[company("U_7", "68.20")],
)


def base(*facts: KnowledgeFact) -> KnowledgeBase:
    return resolve("U_1", facts)


def cards_by_id(
    legends: Legends = LEGENDS, knowledge: KnowledgeBase | None = None
) -> dict[str, LinkedCompanyCard]:
    result = build_cards("U_1", LINKS, knowledge or base(), [], legends)
    return {card.id: card for card in result.cards}


def test_shares_by_amount_and_count_across_whole_direction() -> None:
    result = build_cards("U_1", LINKS, base(), [], Legends())
    assert (result.incoming.amount_rub, result.incoming.payments_count) == (1000.0, 40)
    assert (result.outgoing.amount_rub, result.outgoing.counterparties) == (1000.0, 3)
    cards = {card.id: card for card in result.cards}
    buyer = cards["U_3"].incoming
    assert buyer is not None
    assert (buyer.amount_share, buyer.count_share) == (0.75, 0.75)
    carrier = cards["U_4"].outgoing
    assert carrier is not None
    assert carrier.amount_share == 0.85
    assert carrier.count_share == 0.75
    assert cards["U_3"].outgoing is not None and cards["U_3"].outgoing.amount_share == 0.05


def test_card_merges_payments_and_group() -> None:
    cards = cards_by_id(Legends())
    assert cards["U_2"].origins == [LinkOrigin.PAYMENTS, LinkOrigin.GROUP]
    assert cards["U_2"].segment == "logistics"
    assert cards["U_6"].origins == [LinkOrigin.GROUP]
    assert cards["U_6"].incoming is None and cards["U_6"].outgoing is None
    assert cards["U_7"].origins == [LinkOrigin.SHARED_PERSON]
    order = list(cards)
    assert order[:4] == ["U_4", "U_3", "U_5", "U_2"]
    assert order.index("U_6") < order.index("U_7")


def test_role_guess_from_payments_when_nobody_said() -> None:
    cards = cards_by_id(Legends())
    assert (cards["U_5"].role, cards["U_5"].role_source) == (
        CounterpartyRole.BUYER,
        RoleSource.PAYMENTS,
    )
    assert cards["U_2"].role is CounterpartyRole.SUPPLIER
    assert cards["U_3"].role is None and cards["U_3"].role_source is None
    assert cards["U_6"].role is None


def test_legend_role_and_name() -> None:
    card = cards_by_id()["U_4"]
    assert (card.name, card.name_source) == ("ООО «Полярный рейс»", "legend")
    assert (card.role, card.role_source) == (CounterpartyRole.CARRIER, RoleSource.LEGEND)


def test_client_role_beats_legend() -> None:
    client = new_fact(
        "U_1",
        FactCategory.CUSTOMERS,
        "U_4 — покупатель",
        FactSource.CLIENT,
        key=role_key("U_4"),
        value="buyer",
        now=NOW,
    )
    card = cards_by_id(knowledge=base(client))["U_4"]
    assert (card.role, card.role_source) == (CounterpartyRole.BUYER, RoleSource.CLIENT)
    assert card.role_fact_id == client.id


def test_role_fact_revises_existing_fact() -> None:
    legend = new_fact(
        "U_1",
        FactCategory.SUPPLIERS,
        "ООО «Полярный рейс» (U_4) — перевозчик",
        FactSource.LEGEND,
        key=role_key("U_4"),
        value="carrier",
        evidence=["legend:U_4"],
        now=NOW,
        fact_id="draft:U_1:counterparty.U_4.role",
    )
    knowledge = base(legend)
    card = cards_by_id(knowledge=knowledge)["U_4"]
    fact = role_fact(knowledge, card, CounterpartyRole.SUPPLIER, NOW)
    assert (fact.id, fact.version, fact.source) == (legend.id, 2, FactSource.CLIENT)
    assert fact.text == "ООО «Полярный рейс» (U_4) — поставщик"
    assert fact.value == "supplier"

    fresh = role_fact(base(), cards_by_id(Legends())["U_5"], CounterpartyRole.CARRIER, NOW)
    assert (fresh.key, fresh.version, fresh.text) == (role_key("U_5"), 1, "U_5 — перевозчик")
    assert fresh.category is FactCategory.SUPPLIERS


def test_top_cards_limit_keeps_known_roles() -> None:
    payments = [pay(company(f"U_{100 + index}"), "in", str(1000 - index), 1) for index in range(40)]
    payments.append(pay(company("U_4"), "out", "1", 1))
    result = build_cards("U_1", CompanyLinks(payments=payments), base(), [], LEGENDS)
    ids = {card.id for card in result.cards}
    assert len(ids) == TOP_PAYMENT_CARDS + 1
    assert "U_4" in ids
    assert result.hidden == 41 - len(ids)


def test_no_payments_warning() -> None:
    result = build_cards("U_1", CompanyLinks(), base(), [], Legends())
    assert result.cards == []
    assert result.warnings == ["Платежей нет: доли в обороте не посчитаны"]
    assert result.incoming.amount_rub == 0


def test_manual_company_card_and_profile() -> None:
    link = manual_link(
        "U_1",
        " ООО «Северный терминал» ",
        CounterpartyRole.CARRIER,
        okved_code="52.10",
        note="  ",
        now=NOW,
    )
    card = manual_card(link)
    assert card.company_id is None and card.origins == [LinkOrigin.CLIENT]
    assert (card.name, card.role_source, card.segment) == (
        "ООО «Северный терминал»",
        RoleSource.CLIENT,
        "logistics",
    )
    assert card.note is None
    result = build_cards("U_1", LINKS, base(), [link], Legends())
    assert result.cards[-1] == card

    profile = assemble(CLIENT, CompanyLinks())
    updated = apply_links(profile, [link])
    feature = updated.features[-1]
    assert feature.label == (
        "Со слов клиента: ООО «Северный терминал» — перевозчик, отрасль «Транспорт и логистика»"
    )
    assert feature.source_fields == [f"links:{link.link_id}"]
    exposure = updated.exposures[-1]
    assert exposure.topic is Topic.INDUSTRY
    assert exposure.feature_ids == [feature.id]
    assert "логистика" in exposure.keywords
    assert "тарифов" in exposure.reason


def test_manual_company_without_industry_adds_no_exposure() -> None:
    link = manual_link("U_1", "ИП Иванов", CounterpartyRole.BUYER, now=NOW)
    profile = assemble(CLIENT, CompanyLinks())
    updated = apply_links(profile, [link])
    assert updated.features[-1].label == "Со слов клиента: ИП Иванов — покупатель"
    assert updated.exposures == profile.exposures
    assert apply_links(profile, []) is profile


def test_card_contract_rules() -> None:
    with pytest.raises(ValidationError):
        LinkedCompanyCard(
            id="U_1", company_id="U_1", origins=[LinkOrigin.GROUP], role=CounterpartyRole.BUYER
        )
    with pytest.raises(ValidationError):
        LinkedCompanyCard(id="manual:1", company_id="U_1", origins=[LinkOrigin.CLIENT])
    with pytest.raises(ValidationError):
        LinkedCompanyCard(
            id="U_1",
            company_id="U_1",
            origins=[LinkOrigin.GROUP],
            incoming=Turnover(amount_rub=1, amount_share=1),
        )


def client_role(partner_id: str, role: str) -> KnowledgeFact:
    return new_fact(
        "U_1",
        FactCategory.SUPPLIERS,
        f"{partner_id} — перевозчик",
        FactSource.CLIENT,
        key=role_key(partner_id),
        value=role,
        now=NOW,
    )


def logistics(profile: CompanyProfile) -> list[Exposure]:
    return [item for item in profile.exposures if "грузоперевозки" in item.keywords]


def test_client_carrier_adds_logistics_exposure() -> None:
    links = CompanyLinks(
        payments=[
            pay(company("U_8"), "out", "300", 3),
            pay(company("U_9", "46.38"), "out", "700", 7),
            pay(company("U_10"), "out", "50", 1),
        ]
    )
    plain = assemble(CLIENT, links)
    assert logistics(plain) == []
    knowledge = base(client_role("U_8", "carrier"), client_role("U_10", "carrier"))
    profile = apply_links(apply_knowledge(plain, knowledge), [])
    [exposure] = logistics(profile)
    assert exposure.feature_ids == ["feature:knowledge:counterparty.U_8.role"]
    assert exposure.direction is ExposureDirection.RISK
    assert "U_8 — 29%" in exposure.reason
    assert "U_10" not in exposure.reason


def test_client_carrier_joins_existing_logistics_exposure() -> None:
    links = CompanyLinks(
        payments=[
            pay(company("U_4", "49.41"), "out", "850", 15),
            pay(company("U_9", "46.38"), "out", "150", 1),
        ]
    )
    plain = assemble(CLIENT, links)
    [before] = logistics(plain)
    profile = apply_links(apply_knowledge(plain, base(client_role("U_4", "carrier"))), [])
    [after] = logistics(profile)
    assert after.feature_ids == [*before.feature_ids, "feature:knowledge:counterparty.U_4.role"]
    assert after.reason.startswith(before.reason)
    assert "U_4 — 85%" in after.reason


def test_supplier_role_does_not_add_logistics() -> None:
    links = CompanyLinks(payments=[pay(company("U_8"), "out", "300", 3)])
    plain = assemble(CLIENT, links)
    profile = apply_knowledge(plain, base(client_role("U_8", "supplier")))
    assert apply_links(profile, []) is profile
