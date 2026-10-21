"""Вопросы на уточнение: чего система о компании не знает и что ей нужно подтвердить.

Каталог `SLOTS` — то, что важно для отбора и выводов, но не следует из выгрузки. Вопрос задаётся,
только если он касается этой компании (о доле экспорта — тем, у кого есть ВЭД) и ответа ещё нет.
Формулировки собираются из профиля по шаблонам, без LLM: вопрос не может выдумать факт
о компании, а бюджет модели остаётся на интерпретацию новостей.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from app.contracts.knowledge import (
    FactCategory,
    FactSource,
    FactStatus,
    KnowledgeBase,
    KnowledgeFact,
    KnowledgeGaps,
    Question,
)
from app.contracts.profile import CompanyProfile
from app.profile.knowledge import new_fact, revise
from app.profile.products import listing

Priority = Literal["high", "medium", "low"]
PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}
SEASONAL_SEGMENTS = {
    "agro",
    "food",
    "retail",
    "hospitality",
    "construction",
    "building_materials",
    "consumer_goods",
    "auto",
    "leisure",
    "utilities",
}
NOT_APPLICABLE = "Не относится к компании"


@dataclass(frozen=True)
class Asked:
    question: str
    why: str
    priority: Priority
    based_on: tuple[str, ...] = ()


@dataclass(frozen=True)
class Slot:
    key: str
    category: FactCategory
    ask: Callable[[CompanyProfile, set[str]], Asked | None]


def segment(profile: CompanyProfile) -> str:
    name = profile.industry.segment_name
    return f"в сегменте «{name}»" if profile.industry.segment != "other" and name else "у вас"


def sales_markets(profile: CompanyProfile, features: set[str]) -> Asked:
    region = profile.location.region
    where = f"Компания зарегистрирована в регионе {region}. " if region else ""
    return Asked(
        f"{where}В каких регионах и странах вы продаёте основную часть продукции или услуг?",
        "Региональные и страновые новости будут отбираться по вашим рынкам сбыта, "
        "а не только по месту регистрации",
        "high",
        tuple(ref for ref in ("feature:region", "feature:geography") if ref in features),
    )


def export_share(profile: CompanyProfile, features: set[str]) -> Asked | None:
    if not profile.foreign_trade.has_customs_records:
        return None
    countries = profile.foreign_trade.countries
    partners = f" ({listing(countries)})" if countries else ""
    return Asked(
        f"По данным таможни вы ведёте внешнеэкономическую деятельность{partners}. "
        "Какая доля выручки приходится на экспорт и какая доля закупок — на импорт?",
        "От этого зависит, чем для вас станет движение курса: риском для закупок "
        "или возможностью для экспортной выручки",
        "high",
        ("feature:customs",),
    )


def seasonality(profile: CompanyProfile, features: set[str]) -> Asked:
    seasonal = profile.industry.segment in SEASONAL_SEGMENTS
    return Asked(
        f"Есть ли {segment(profile)} сезонность: в какие месяцы пик продаж "
        "и когда больше всего нужны оборотные деньги?",
        "Новости о ставке, курсе и ценах на сырьё важнее перед сезоном закупок: "
        "сводка учтёт ваш календарь",
        "high" if seasonal else "low",
        ("feature:industry",) if "feature:industry" in features else (),
    )


def products(profile: CompanyProfile, features: set[str]) -> Asked:
    activity = profile.industry.okved_name
    stated = f"Основной вид деятельности по ОКВЭД — «{activity}». " if activity else ""
    return Asked(
        f"{stated}Какие продукты или услуги дают основную часть выручки?",
        "Код ОКВЭД описывает деятельность грубо: по вашим продуктам отбор найдёт новости "
        "именно ваших рынков",
        "medium",
        ("feature:industry",) if "feature:industry" in features else (),
    )


def partners(
    role: Literal["customers", "suppliers"],
) -> Callable[[CompanyProfile, set[str]], Asked]:
    title = "покупатели" if role == "customers" else "поставщики"
    feature = (
        "feature:counterparties_incoming"
        if role == "customers"
        else ("feature:counterparties_outgoing")
    )
    consequence = (
        "спад у крупного покупателя бьёт по вашей выручке"
        if role == "customers"
        else "перебои и рост цен у поставщика бьют по вашим затратам"
    )

    def ask(profile: CompanyProfile, features: set[str]) -> Asked:
        return Asked(
            f"Кто ваши ключевые {title} и из каких они отраслей? Можно без названий: "
            "отрасль и примерная доля оборота",
            f"Новость про рынок контрагента часто важнее отраслевой: {consequence}",
            "medium",
            (feature,) if feature in features else (),
        )

    return ask


def constraints(profile: CompanyProfile, features: set[str]) -> Asked:
    return Asked(
        "Что сейчас сильнее всего ограничивает бизнес: кадры, логистика, доступ "
        "к финансированию, регулирование, что-то ещё?",
        "Сводка поднимет новости о том, что для вас узкое место",
        "medium",
    )


def plans(profile: CompanyProfile, features: set[str]) -> Asked:
    debt = " и финансирование" if "feature:debt_dependence" in features else ""
    return Asked(
        "Какие планы на ближайший год: инвестиции, новые рынки, крупные закупки" f"{debt}?",
        "Планы меняют, что для вас важно: перед кредитом — ставка, перед выходом "
        "на новый рынок — его регулирование",
        "low",
        ("feature:debt_dependence",) if debt else (),
    )


SLOTS = (
    Slot("markets.sales_regions", FactCategory.MARKETS, sales_markets),
    Slot("markets.export_share", FactCategory.MARKETS, export_share),
    Slot("seasonality.peak", FactCategory.SEASONALITY, seasonality),
    Slot("products.main", FactCategory.PRODUCTS, products),
    Slot("customers.main", FactCategory.CUSTOMERS, partners("customers")),
    Slot("suppliers.main", FactCategory.SUPPLIERS, partners("suppliers")),
    Slot("constraints.main", FactCategory.CONSTRAINTS, constraints),
    Slot("plans.next_year", FactCategory.PLANS, plans),
)
SLOT_BY_KEY = {slot.key: slot for slot in SLOTS}


def gaps(profile: CompanyProfile, base: KnowledgeBase) -> KnowledgeGaps:
    """Вопросы о том, чего нет в базе знаний, и предположения, которые стоит подтвердить."""
    known_keys = {fact.key for fact in base.facts if fact.key}
    features = {feature.id for feature in profile.features}
    questions = []
    for slot in SLOTS:
        if slot.key in known_keys:
            continue
        asked = slot.ask(profile, features)
        if asked is None:
            continue
        questions.append(
            Question(
                key=slot.key,
                category=slot.category,
                question=asked.question,
                why=asked.why,
                priority=asked.priority,
                based_on=[ref for ref in asked.based_on if ref in features],
            )
        )
    questions.sort(key=lambda question: PRIORITY_ORDER[question.priority])
    return KnowledgeGaps(
        company_id=profile.company_id,
        questions=questions,
        to_confirm=[fact for fact in base.facts if fact.status is FactStatus.ASSUMPTION],
        known=sum(1 for fact in base.facts if fact.source is FactSource.CLIENT),
    )


def answer(
    base: KnowledgeBase,
    key: str,
    text: str,
    value: str | float | bool | None = None,
    *,
    now: datetime | None = None,
) -> KnowledgeFact:
    """Ответ на вопрос как факт клиента. Если по ключу уже есть факт, ответ — его новая версия:
    у одного ключа одна история, а не два конкурирующих факта."""
    slot = SLOT_BY_KEY.get(key)
    if slot is None:
        raise KeyError(key)
    current = next((fact for fact in base.facts if fact.key == key), None)
    if current is not None:
        return revise(current, now, source=FactSource.CLIENT, text=text, value=value)
    return new_fact(
        base.company_id, slot.category, text, FactSource.CLIENT, key=key, value=value, now=now
    )
