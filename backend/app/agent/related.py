"""A5-15: событие у конкурента или контрагента — выжимка и «что это значит для нас».

Связь клиента с компанией из новости становится отдельным признаком профиля: конкурент на том
же рынке (с основаниями из списка конкурентов) или контрагент с ролью и долей платежей. Агент
разбирает событие с этим признаком: «что произошло» — выжимка новости о компании, а «почему
важно» и последствия — что это значит для клиента через эту связь. Критик не пропускает вывод
о важности, который на связь не опирается.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from app.contracts.competitors import Competitors
from app.contracts.links import LinkedCompanies, LinkedCompanyCard, LinkOrigin
from app.contracts.news import NewsItem, Topic
from app.contracts.profile import (
    CompanyProfile,
    CounterpartyRole,
    Exposure,
    ExposureDirection,
    FeatureKind,
    ProfileFeature,
)
from app.contracts.related_feed import CompanyMention
from app.news.entities import canonical
from app.rank.related import Target, mentions

RELATION_PREFIX = "feature:relation:"
MAX_REASONS = 2
ROLE_WORDS = {
    CounterpartyRole.BUYER: ("покупатель", "покупателя"),
    CounterpartyRole.SUPPLIER: ("поставщик", "поставщика"),
    CounterpartyRole.CARRIER: ("перевозчик", "перевозчика"),
}
CONSEQUENCES = {
    CounterpartyRole.BUYER: "спрос и платёжная дисциплина покупателя меняют вашу выручку",
    CounterpartyRole.SUPPLIER: "цены, сроки и перебои у поставщика меняют ваши затраты",
    CounterpartyRole.CARRIER: "тарифы и перебои у перевозчика меняют сроки и затраты",
}
COMPETITOR_CONSEQUENCE = (
    "вы работаете на одном рынке: сделки, цены и проблемы конкурента меняют расклад сил"
)


@dataclass(frozen=True)
class Relation:
    mention: CompanyMention
    feature: ProfileFeature
    exposure: Exposure
    role: CounterpartyRole | None = None

    @property
    def subject(self) -> str:
        if self.mention.relationship == "competitor":
            return f"конкурента «{self.mention.name}»"
        word = ROLE_WORDS[self.role][1] if self.role else "контрагента"
        return f"{word} «{self.mention.name}»"


def share(value: float) -> str:
    return f"{value * 100:.0f}%"


def role_of(card: LinkedCompanyCard) -> CounterpartyRole | None:
    if card.role:
        return card.role
    if card.incoming and not card.outgoing:
        return CounterpartyRole.BUYER
    if card.outgoing and not card.incoming:
        return CounterpartyRole.SUPPLIER
    return None


def eligible(card: LinkedCompanyCard) -> bool:
    return bool(
        LinkOrigin.PAYMENTS in card.origins or LinkOrigin.CLIENT in card.origins or card.role
    )


def target_of(
    company_id: str, competitors: Competitors, links: LinkedCompanies
) -> tuple[Target, str, list[str]] | None:
    for competitor in competitors.competitors:
        if competitor.id == company_id and competitor.name:
            reasons = "; ".join(competitor.reasons[:MAX_REASONS])
            target = Target(
                competitor.id, competitor.name, canonical(competitor.name), "competitor"
            )
            return (
                target,
                f"Конкурент на вашем рынке: {competitor.name}. {reasons}",
                [f"competitors:{competitor.id}"],
            )
    for card in links.cards:
        if card.id != company_id or not card.name or not eligible(card):
            continue
        role = role_of(card)
        word = ROLE_WORDS[role][0].capitalize() if role else "Контрагент"
        flows = []
        if card.incoming:
            flows.append(f"платит вам {share(card.incoming.amount_share)} входящих платежей")
        if card.outgoing:
            flows.append(f"получает {share(card.outgoing.amount_share)} ваших платежей")
        detail = ", ".join(flows) or (card.note or "связь указана клиентом")
        target = Target(card.id, card.name, canonical(card.name), "counterparty")
        return target, f"{word}: {card.name} — {detail}", [f"links:{card.id}"]
    return None


def relation_for(
    company_id: str,
    news: Sequence[NewsItem],
    competitors: Competitors,
    links: LinkedCompanies,
) -> Relation | None:
    """Связь с компанией, если она есть в списках клиента и упомянута в выбранных материалах."""
    found = target_of(company_id, competitors, links)
    if found is None or not found[0].key:
        return None
    target, label, fields = found
    mention = next((m for item in news for m in mentions(item, [target])), None)
    if mention is None:
        return None
    card = next((c for c in links.cards if c.id == company_id), None)
    role = role_of(card) if card and target.relationship == "counterparty" else None
    if card is not None and target.relationship == "counterparty":
        mention = mention.model_copy(update={"origins": card.origins, "role": card.role})
    feature = ProfileFeature(
        id=f"{RELATION_PREFIX}{company_id}",
        kind=FeatureKind.COMPETITOR
        if target.relationship == "competitor"
        else FeatureKind.COUNTERPARTY,
        label=label,
        value=role.value if role else target.relationship,
        source_fields=fields,
    )
    exposure = Exposure(
        topic=Topic.COMPANY,
        direction=ExposureDirection.MIXED,
        reason=CONSEQUENCES[role] if role else COMPETITOR_CONSEQUENCE,
        feature_ids=[feature.id],
        keywords=[target.name],
    )
    return Relation(mention, feature, exposure, role)


def with_relation(profile: CompanyProfile, relation: Relation) -> CompanyProfile:
    features = [f for f in profile.features if f.id != relation.feature.id]
    return profile.model_copy(
        update={
            "features": [*features, relation.feature],
            "exposures": [*profile.exposures, relation.exposure],
        }
    )


def focus_for(relation: Relation, focus: str | None) -> str:
    text = (
        f"Событие у {relation.subject}. «Что произошло» — короткая выжимка новости о "
        f"{relation.mention.name}. «Почему важно», риски, возможности и действия — что это "
        f"значит для нашей компании через связь {relation.feature.id}; ссылайся на этот признак"
    )
    return f"{text}. {focus}" if focus else text


def relation_ids(profile: CompanyProfile) -> set[str]:
    return {feature.id for feature in profile.features if feature.id.startswith(RELATION_PREFIX)}
