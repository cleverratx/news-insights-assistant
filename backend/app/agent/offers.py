"""A5-12: продукт банка внутри интерпретации — только когда он следует из события и компании.

Предложение строится по правилам, а не моделью: продукт уместен, если тема события совпадает
с тем, на что продукт отвечает, у компании есть признак-основание и в готовом инсайте есть риск
или возможность, к которым продукт относится. Не больше одного продукта на инсайт, ни одного —
для малозначимых событий. Если продукт уже подключён, речь о пересмотре условий, а не о продаже.
Модель тоже может предложить продукт действием `bank_product`, но критик пропускает только
продукты из того же списка уместных.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from app.contracts.common import BankProduct, Significance
from app.contracts.generation import OfferAction, ProductOffer
from app.contracts.insight import (
    Action,
    ActionKind,
    CheckIssue,
    CheckProblem,
    Impact,
    Insight,
    InsightStatus,
)
from app.contracts.news import NewsItem, Topic
from app.contracts.profile import CompanyProfile

DISCLAIMER = (
    "Не является инвестиционной рекомендацией. Условия и возможность подключения "
    "определяются после консультации с менеджером банка."
)
MAX_LABELS = 2


@dataclass(frozen=True)
class Rule:
    product: BankProduct
    title: str
    topics: frozenset[Topic]
    evidence: tuple[str, ...]
    situation: str
    benefit: str
    renewal: str
    segments: frozenset[str] = frozenset()


RULES = (
    Rule(
        BankProduct.LOAN,
        "Пересмотр условий долга и рефинансирование",
        frozenset({Topic.KEY_RATE, Topic.DEBT_MARKET}),
        ("feature:debt_dependence", "feature:loan", "feature:finance_payments"),
        "Событие меняет стоимость заёмных денег, а компания опирается на заёмное финансирование",
        "Менеджер сравнит условия текущего долга с рыночными и предложит структуру: "
        "фиксированная или плавающая ставка, срок, график погашения",
        "Кредит уже есть в Альфа-Банке: речь о пересмотре условий, а не о новом долге",
    ),
    Rule(
        BankProduct.DEPOSIT,
        "Размещение свободной ликвидности",
        frozenset({Topic.KEY_RATE, Topic.INFLATION}),
        ("feature:deposit",),
        "Уровень ставок меняет доходность свободных средств компании",
        "Срок и сумму размещения можно подобрать под календарь платежей компании",
        "Депозит уже есть: можно пересмотреть срок и сумму размещения",
    ),
    Rule(
        BankProduct.GUARANTEE,
        "Гарантия вместо предоплаты и залогов по ВЭД",
        frozenset({Topic.TRADE, Topic.SANCTIONS, Topic.REGULATION}),
        ("feature:customs",),
        "Событие меняет условия внешней торговли, а компания ведёт ВЭД",
        "Гарантия банка для таможни или поставщика не замораживает оборотные деньги "
        "в предоплате и залогах",
        "Гарантии уже есть: можно расширить лимит под новые условия поставок",
    ),
    Rule(
        BankProduct.SETTLEMENT,
        "Расчёты по ВЭД и валютный контроль",
        frozenset({Topic.FX, Topic.SANCTIONS, Topic.TRADE}),
        ("feature:payment_hub_trade", "feature:unfriendly_trade"),
        "Событие затрагивает платежи и курс, а партнёры компании — в странах со сложными "
        "расчётами",
        "Валютный контроль банка поможет выбрать валюту и маршрут платежей, чтобы снизить "
        "риск задержек и возвратов",
        "Счёт уже в Альфа-Банке: менеджер проверит маршруты текущих платежей",
    ),
    Rule(
        BankProduct.FACTORING,
        "Факторинг: деньги за отгрузку без ожидания отсрочки",
        frozenset({Topic.CONSUMPTION, Topic.INDUSTRY, Topic.COMPANY, Topic.KEY_RATE}),
        ("feature:customer_concentration", "feature:factoring"),
        "Событие может затянуть оплату от покупателей, а выручка компании зависит "
        "от немногих покупателей",
        "Факторинг закрывает разрыв между отгрузкой и оплатой, не увеличивая кредитную линию",
        "Факторинг уже подключён: можно пересмотреть лимит по ключевым покупателям",
    ),
    Rule(
        BankProduct.LEASING,
        "Лизинг транспорта и техники",
        frozenset({Topic.LOGISTICS, Topic.COMMODITIES, Topic.KEY_RATE}),
        ("feature:leasing",),
        "Событие меняет стоимость перевозок и техники, а бизнес компании на них держится",
        "Лизинг обновляет транспорт и технику без единовременной нагрузки на оборотные "
        "средства; платежи фиксируются в договоре",
        "Лизинг уже есть: можно обсудить обновление парка на текущих условиях",
        frozenset({"logistics", "construction", "agro", "auto"}),
    ),
)
RULE_BY_PRODUCT = {rule.product: rule for rule in RULES}


def topics_of(news: Iterable[NewsItem]) -> set[Topic]:
    return {topic for item in news if item.classification for topic in item.classification.topics}


def grounds(rule: Rule, profile: CompanyProfile) -> list[str]:
    present = {feature.id for feature in profile.features}
    found = [feature for feature in rule.evidence if feature in present]
    if not found and profile.industry.segment in rule.segments and "feature:industry" in present:
        found = ["feature:industry"]
    return found


def exposed(rule: Rule, features: list[str], profile: CompanyProfile, topics: set[Topic]) -> bool:
    return any(
        exposure.topic in rule.topics & topics and set(exposure.feature_ids) & set(features)
        for exposure in profile.exposures
    )


def candidates(profile: CompanyProfile, news: Sequence[NewsItem]) -> list[tuple[Rule, list[str]]]:
    topics = topics_of(news)
    result = []
    for rule in RULES:
        if not rule.topics & topics:
            continue
        found = grounds(rule, profile)
        if found:
            result.append((rule, found))
    return result


def prompt_hint(profile: CompanyProfile, news: Sequence[NewsItem]) -> str:
    found = candidates(profile, news)
    if not found:
        return "Продукты банка в действиях не предлагай: оснований в теме и профиле нет"
    listed = "; ".join(
        f"{rule.product.value} — {rule.title} (признаки: {', '.join(features)})"
        for rule, features in found
    )
    return (
        f"Уместные продукты банка: {listed}. Действие kind=bank_product — не больше одного, "
        "только из этого списка и только если продукт отвечает на названный риск или "
        "возможность; иначе продукт не предлагай"
    )


def product_issues(
    actions: Iterable[Action], profile: CompanyProfile, news: Sequence[NewsItem]
) -> list[CheckIssue]:
    allowed = {rule.product for rule, _ in candidates(profile, news)}
    offered = [a for a in actions if a.kind is ActionKind.BANK_PRODUCT and a.product]
    problems = [
        CheckIssue(
            statement_id=action.id,
            problem=CheckProblem.UNSUPPORTED,
            detail=(
                f"Продукт {action.product.value} не следует из темы события и признаков "
                "компании: убери его или замени действием без продукта"
            ),
            resolved=False,
        )
        for action in offered
        if action.product is not None and action.product not in allowed
    ]
    if len(offered) > 1:
        problems.append(
            CheckIssue(
                problem=CheckProblem.OTHER,
                detail="Не больше одного продукта банка на инсайт",
                resolved=False,
            )
        )
    return problems


def relevant(
    rule: Rule, features: list[str], insight: Insight, news: Sequence[NewsItem]
) -> list[Impact]:
    refs = {source.id: source.ref_id for source in insight.sources}
    matching_news = {
        item.id
        for item in news
        if item.classification and rule.topics & set(item.classification.topics)
    }
    return [
        impact
        for impact in [*insight.risks, *insight.opportunities]
        if {refs.get(ref) for ref in impact.source_ids} & (set(features) | matching_news)
    ]


def offer_for(
    insight: Insight, profile: CompanyProfile, news: Sequence[NewsItem]
) -> ProductOffer | None:
    if insight.status is not InsightStatus.READY or insight.significance is Significance.LOW:
        return None
    proposed = {
        action.product
        for action in insight.actions
        if action.kind is ActionKind.BANK_PRODUCT and action.product is not None
    }
    best: tuple[int, Rule, list[str], list[Impact]] | None = None
    for rule, features in candidates(profile, news):
        impacts = relevant(rule, features, insight, news)
        if not impacts:
            continue
        refs = {source.id: source.ref_id for source in insight.sources}
        grounded = sum(
            1 for impact in impacts if {refs.get(r) for r in impact.source_ids} & set(features)
        )
        if not grounded and not exposed(rule, features, profile, topics_of(news)):
            continue
        rank = 2 * grounded + len(impacts) + (3 if rule.product in proposed else 0)
        if best is None or rank > best[0]:
            best = (rank, rule, features, impacts)
    if best is None:
        return None
    _, rule, features, impacts = best
    labels = {feature.id: feature.label for feature in profile.features}
    basis = "; ".join(labels[feature] for feature in features[:MAX_LABELS] if feature in labels)
    held = rule.product in profile.products
    return ProductOffer(
        product=rule.product,
        title=rule.title,
        why=f"{rule.situation}. Основание: {basis}" if basis else rule.situation,
        benefit=f"{rule.renewal}. {rule.benefit}" if held else rule.benefit,
        statement_ids=[impact.id for impact in impacts],
        feature_ids=features,
        already_client=held,
        next_actions=[OfferAction.CONTACT_MANAGER, OfferAction.CALLBACK]
        if held
        else [OfferAction.CONTACT_MANAGER, OfferAction.APPLICATION, OfferAction.CALLBACK],
        disclaimer=DISCLAIMER,
    )
