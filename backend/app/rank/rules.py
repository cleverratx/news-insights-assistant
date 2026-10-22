"""Правила сопоставления новости с профилем компании (A4-01): отрасль новости и ОКВЭД,
отрасли контрагентов и компаний группы, регион, продукты банка, размер бизнеса и ВЭД.

Каждое сработавшее правило ссылается на признак профиля (`ProfileFeature.id`) и объясняет
словами, почему новость касается компании: из этого собирается «почему это важно вам», а
жюри видит, какие признаки компании повлияли на отбор. Правила детерминированные и без
модели: отбор работает при исчерпанном бюджете, а результат одинаков на любых новостях.

Оценка — вероятностное «или» по лучшему срабатыванию каждого правила: два независимых
повода поднимают новость выше одного. Новость о мерах для МСП крупному бизнесу не подходит,
поэтому такое несовпадение размера штрафует оценку, а не просто не добавляет её.
"""

import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from app.contracts.common import BankProduct
from app.contracts.news import Classification, NewsItem, Topic
from app.contracts.profile import CompanyProfile, SizeCategory
from app.contracts.rank import FeatureMatch
from app.profile.okved import SEGMENTS, normalize_code
from app.profile.products import PAYMENT_HUBS, UNFRIENDLY, country_title
from app.rank.regions import country, presence, same_region
from app.search.engine import tokens


class Rule(StrEnum):
    INDUSTRY = "industry"
    COUNTERPARTY = "counterparty"
    GROUP = "group"
    REGION = "region"
    PRODUCT = "product"
    EXPOSURE = "exposure"
    ENTITY = "entity"
    SCALE = "scale"
    FOREIGN_TRADE = "foreign_trade"


INDUSTRY_CODE = 0.75
INDUSTRY_SEGMENT = 0.65
INDUSTRY_WORDS = 0.45
INDUSTRY_TOPIC = 0.2
COUNTERPARTY_BASE = 0.2
COUNTERPARTY_SHARE = 0.4
COUNTERPARTY_MAX = 0.6
GROUP_SEGMENT = 0.4
SHARED_PERSON_SEGMENT = 0.2
REGION_HOME = 0.45
REGION_PARTNER = 0.25
REGION_MARKET = 0.35
PRODUCT = 0.35
EXPOSURE = 0.3
ENTITY = 0.6
FOREIGN_TRADE = 0.5
FOREIGN_TRADE_RISK = 0.4
SCALE_MATCH = 0.2
SCALE_MISMATCH = 0.3

TOPIC_TITLES = {
    Topic.KEY_RATE: "ключевая ставка",
    Topic.INFLATION: "инфляция",
    Topic.FX: "курс рубля",
    Topic.COMMODITIES: "цены на сырьё",
    Topic.TRADE: "внешняя торговля",
    Topic.LOGISTICS: "логистика",
    Topic.SANCTIONS: "санкции",
    Topic.REGULATION: "регулирование",
    Topic.TAXES: "налоги",
    Topic.LABOR: "рынок труда",
    Topic.CONSUMPTION: "потребительский спрос",
    Topic.DEBT_MARKET: "долговой рынок",
    Topic.MACRO: "макроэкономика",
    Topic.INDUSTRY: "события отрасли",
    Topic.REGIONAL: "события региона",
    Topic.COMPANY: "события компаний",
    Topic.OTHER: "прочее",
}
SPECIFIC_TOPICS = frozenset({Topic.INDUSTRY, Topic.REGIONAL, Topic.COMPANY})
GENERIC_STEMS = frozenset({"отрас", "рынок", "рынка", "рынке", "компа", "секто", "прочи"})
PRODUCT_TOPICS: dict[BankProduct, tuple[frozenset[Topic], str]] = {
    BankProduct.LOAN: (
        frozenset({Topic.KEY_RATE, Topic.DEBT_MARKET}),
        "Кредиты в Альфа-Банке: от ставок зависит стоимость долга",
    ),
    BankProduct.LEASING: (
        frozenset({Topic.KEY_RATE, Topic.DEBT_MARKET}),
        "Лизинг в Альфа-Банке: от ставок зависят лизинговые платежи",
    ),
    BankProduct.FACTORING: (
        frozenset({Topic.KEY_RATE, Topic.DEBT_MARKET}),
        "Факторинг в Альфа-Банке: от ставок зависит цена отсрочки покупателям",
    ),
    BankProduct.GUARANTEE: (
        frozenset({Topic.KEY_RATE, Topic.DEBT_MARKET}),
        "Гарантии в Альфа-Банке: условия долгового рынка меняют их стоимость",
    ),
    BankProduct.DEPOSIT: (
        frozenset({Topic.KEY_RATE, Topic.INFLATION, Topic.DEBT_MARKET}),
        "Депозиты в Альфа-Банке: ставка и инфляция определяют реальную доходность свободных денег",
    ),
    BankProduct.PAYROLL: (
        frozenset({Topic.LABOR, Topic.TAXES}),
        "Зарплатный проект: зарплаты, кадры и НДФЛ меняют расходы на персонал",
    ),
}
SME_NEWS = re.compile(
    r"\bмсп\b|мал\w* и средн\w* (?:бизнес|предприним)|мал\w* бизнес|самозанят"
    r"|индивидуальн\w* предпринимател"
)
LARGE_NEWS = re.compile(
    r"крупн\w* (?:бизнес|компани|предприяти|налогоплательщик|заёмщик|заемщик)"
    r"|системообразующ|крупнейш\w* компани"
)
LARGE_SIZES = frozenset({SizeCategory.LARGE, SizeCategory.LARGEST})


class Add(Protocol):
    def __call__(
        self, rule: Rule, weight: float, feature_id: str, reason: str, *terms: str
    ) -> None: ...


@dataclass(frozen=True)
class RuleHit:
    rule: Rule
    weight: float
    feature_id: str
    reason: str
    terms: tuple[str, ...] = ()


@dataclass
class Matching:
    """Итог правил по паре «компания — новость»: что сработало, штраф и оговорки."""

    hits: list[RuleHit] = field(default_factory=list)
    penalty: float = 1.0
    penalties: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def score(self) -> float:
        best: dict[Rule, float] = {}
        for hit in self.hits:
            best[hit.rule] = max(best.get(hit.rule, 0.0), hit.weight)
        missed = 1.0
        for weight in best.values():
            missed *= 1 - weight
        return round((1 - missed) * self.penalty, 4)

    @property
    def rules(self) -> list[Rule]:
        return list(dict.fromkeys(hit.rule for hit in self.hits))

    def features(self) -> list[FeatureMatch]:
        """Признаки для `RankedItem.matched_features`: без повторов, самые весомые первыми."""
        chosen: dict[str, RuleHit] = {}
        for hit in sorted(self.hits, key=lambda item: item.weight, reverse=True):
            chosen.setdefault(hit.feature_id, hit)
        return [
            FeatureMatch(feature_id=hit.feature_id, reason=hit.reason) for hit in chosen.values()
        ]

    def terms(self) -> list[str]:
        return list(dict.fromkeys(term for hit in self.hits for term in hit.terms))


def match(profile: CompanyProfile, news: NewsItem) -> Matching:
    result = Matching()
    known = {feature.id for feature in profile.features}
    classification = news.classification
    if classification is None:
        result.warnings.append("Новость не классифицирована: правила отбора не применялись")
        return result
    if classification.method == "rules":
        result.warnings.append("Разбор новости по словарю, а не моделью: совпадения грубее")

    def add(rule: Rule, weight: float, feature_id: str, reason: str, *terms: str) -> None:
        if feature_id in known:
            result.hits.append(RuleHit(rule, round(weight, 4), feature_id, reason, terms))

    industry_rules(profile, classification, add)
    counterparty_rules(profile, classification, add)
    region_rules(profile, classification, add)
    product_rules(profile, classification, add)
    exposure_rules(profile, classification, add)
    entity_rules(profile, news, classification, add)
    foreign_trade_rules(profile, classification, add)
    scale_rules(profile, news, result, add)
    return result


def segment_title(segment: str) -> str:
    return SEGMENTS[segment].name if segment in SEGMENTS else segment


def stems(text: str) -> set[str]:
    return {stem for stem in tokens(text) if stem not in GENERIC_STEMS}


def industry_rules(profile: CompanyProfile, classification: Classification, add: Add) -> None:
    industry = profile.industry
    if industry.segment == "other":
        return
    own_code = normalize_code(industry.okved_code)
    for raw in classification.okved_codes:
        code = normalize_code(raw)
        if own_code and code and (own_code.startswith(code) or code.startswith(own_code[:2])):
            add(
                Rule.INDUSTRY,
                INDUSTRY_CODE,
                "feature:industry",
                f"Новость об отрасли с ОКВЭД {code}, основной ОКВЭД компании — {own_code}",
                code,
            )
    title = segment_title(industry.segment)
    if industry.segment in classification.segments:
        add(
            Rule.INDUSTRY,
            INDUSTRY_SEGMENT,
            "feature:industry",
            f"Новость о рынке «{title}», на котором работает компания",
            title,
        )
        return
    vocabulary = stems(" ".join([title, *industry.keywords]))
    for phrase in classification.industries:
        common = stems(phrase) & vocabulary
        if common:
            add(
                Rule.INDUSTRY,
                INDUSTRY_WORDS,
                "feature:industry",
                f"Новость об отрасли «{phrase}» — это рынок компании «{title}»",
                phrase,
            )
            return
    if classification.segments:
        return
    topics = [topic for topic in classification.topics if topic in industry.topics]
    if topics:
        add(
            Rule.INDUSTRY,
            INDUSTRY_TOPIC,
            "feature:industry",
            f"Рынок «{title}» чувствителен к таким событиям: "
            + ", ".join(TOPIC_TITLES[topic] for topic in topics),
        )


def counterparty_rules(profile: CompanyProfile, classification: Classification, add: Add) -> None:
    segments = set(classification.segments) - {profile.industry.segment, "other"}
    if not segments:
        return
    shares: defaultdict[tuple[str, str], float] = defaultdict(float)
    for partner in profile.counterparties:
        segment = partner.segment or (partner.legend.segment if partner.legend else None)
        if segment in segments:
            shares[(partner.direction, segment)] += partner.share or 0.0
    roles = {"incoming": "покупатели", "outgoing": "поставщики"}
    for (direction, segment), share in shares.items():
        title = segment_title(segment)
        add(
            Rule.COUNTERPARTY,
            min(COUNTERPARTY_BASE + COUNTERPARTY_SHARE * share, COUNTERPARTY_MAX),
            f"feature:counterparties_{direction}",
            f"Новость о рынке «{title}», где работают ваши {roles[direction]}: "
            f"{share * 100:.0f}% оборота этого направления",
            title,
        )
    if profile.group:
        for member in profile.group.members:
            if member.segment in segments:
                title = segment_title(member.segment)
                add(
                    Rule.GROUP,
                    GROUP_SEGMENT,
                    "feature:group_segments",
                    f"Новость о рынке «{title}», где работают компании вашей группы",
                    title,
                )
                add(
                    Rule.GROUP,
                    GROUP_SEGMENT,
                    "feature:group",
                    f"Компании группы работают на рынке «{title}», о котором новость",
                    title,
                )
    for company in profile.related_companies:
        if company.segment in segments:
            title = segment_title(company.segment)
            add(
                Rule.GROUP,
                SHARED_PERSON_SEGMENT,
                "feature:person_links",
                f"Компания, связанная с вашей через общих лиц, работает на рынке «{title}»",
                title,
            )


def region_rules(profile: CompanyProfile, classification: Classification, add: Add) -> None:
    """Регион новости и места присутствия компании (A4-10): регистрация, рынки со слов
    клиента, контрагенты, группа, связанные компании и лица."""
    if not classification.regions:
        return
    places = [place for place in presence(profile) if place.source != "trade"]
    for region in classification.regions:
        local = [p for p in places if p.source == "home" and same_region(p.name, region)]
        for place in local:
            add(
                Rule.REGION,
                REGION_HOME,
                place.feature_id or "feature:region",
                f"Новость о регионе «{region}», где зарегистрирована компания",
                region,
            )
        if local:
            continue
        markets = [p for p in places if p.source == "market" and same_region(p.name, region)]
        for place in markets:
            add(
                Rule.REGION,
                REGION_MARKET,
                place.feature_id or "feature:geography",
                f"Новость о регионе «{region}», где у компании рынок сбыта, со слов клиента",
                region,
            )
        if markets:
            continue
        if any(p.source != "home" and same_region(p.name, region) for p in places):
            add(
                Rule.REGION,
                REGION_PARTNER,
                "feature:geography",
                f"Новость о регионе «{region}», где работают ваши контрагенты, "
                "компании группы или связанные лица",
                region,
            )


def product_rules(profile: CompanyProfile, classification: Classification, add: Add) -> None:
    topics = set(classification.topics)
    for product in profile.products:
        if product not in PRODUCT_TOPICS:
            continue
        sensitive, reason = PRODUCT_TOPICS[product]
        if topics & sensitive:
            add(Rule.PRODUCT, PRODUCT, f"feature:{product.value}", reason)


def exposure_rules(profile: CompanyProfile, classification: Classification, add: Add) -> None:
    topics = set(classification.topics) - SPECIFIC_TOPICS
    for exposure in profile.exposures:
        if exposure.topic in topics:
            for feature_id in exposure.feature_ids:
                add(Rule.EXPOSURE, EXPOSURE, feature_id, exposure.reason)


def mentions(names: Iterable[str], keyword: str) -> bool:
    needle = keyword.lower().replace("ё", "е").strip()
    if len(needle) < 3:
        return False
    pattern = re.compile(rf"(?<![а-яa-z0-9]){re.escape(needle)}")
    return any(pattern.search(name.lower().replace("ё", "е")) for name in names)


def entity_rules(
    profile: CompanyProfile, news: NewsItem, classification: Classification, add: Add
) -> None:
    names = [*classification.entities, news.title]
    for exposure in profile.exposures:
        if exposure.topic is not Topic.COMPANY:
            continue
        for keyword in exposure.keywords:
            if mentions(names, keyword):
                for feature_id in exposure.feature_ids:
                    add(
                        Rule.ENTITY,
                        ENTITY,
                        feature_id,
                        f"Новость о компании «{keyword}» из вашего окружения: {exposure.reason}",
                        keyword,
                    )


def foreign_trade_rules(profile: CompanyProfile, classification: Classification, add: Add) -> None:
    partners = {country(name): name for name in profile.foreign_trade.countries}
    if not partners:
        return
    found = [partners[key] for key in map(country, classification.countries) if key in partners]
    for name in dict.fromkeys(found):
        title = country_title(name)
        add(
            Rule.FOREIGN_TRADE,
            FOREIGN_TRADE,
            "feature:customs",
            f"Новость о стране «{title}», с которой компания ведёт ВЭД по данным ФТС",
            title,
        )
        if name in UNFRIENDLY or country(name) in UNFRIENDLY:
            add(
                Rule.FOREIGN_TRADE,
                FOREIGN_TRADE_RISK,
                "feature:unfriendly_trade",
                f"{title} — в перечне недружественных стран: условия торговли могут ужесточаться",
                title,
            )
        if name in PAYMENT_HUBS:
            add(
                Rule.FOREIGN_TRADE,
                FOREIGN_TRADE_RISK,
                "feature:payment_hub_trade",
                f"Через {title} идут расчёты по ВЭД: события там влияют на сроки платежей",
                title,
            )


def scale_rules(profile: CompanyProfile, news: NewsItem, result: Matching, add: Add) -> None:
    size = profile.scale.size_category
    if size is None:
        return
    text = " ".join(filter(None, (news.title, news.summary))).lower().replace("ё", "е")
    sme, large = bool(SME_NEWS.search(text)), bool(LARGE_NEWS.search(text))
    if sme == large:
        return
    company_large = size in LARGE_SIZES
    if large and company_large:
        add(
            Rule.SCALE, SCALE_MATCH, "feature:size", "Новость о крупном бизнесе, компания — крупная"
        )
    elif sme and not company_large:
        add(
            Rule.SCALE,
            SCALE_MATCH,
            "feature:size",
            "Новость о малом и среднем бизнесе, как компания",
        )
    else:
        result.penalty = SCALE_MISMATCH
        result.penalties.append(
            "Новость о мерах для малого и среднего бизнеса, а компания — крупная"
            if sme
            else "Новость о крупном бизнесе, а компания — малая или средняя"
        )
