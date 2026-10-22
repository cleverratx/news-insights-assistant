"""Региональная значимость (A4-10): где происходит событие относительно компании.

Присутствие компании собирается из профиля: регион и город регистрации, регионы
контрагентов, компаний группы, связанных компаний и лиц, рынки из базы знаний (что клиент
сказал о регионах продаж) и страны ВЭД. Новость получает охват:

    home          про регион регистрации
    presence      про регион или страну, где есть контрагенты, группа, рынки или ВЭД
    national      про всю страну, без привязки к месту или место в ней второстепенно
    other_region  региональное событие там, где у компании ничего нет

Новость other_region в ленте опускается под все остальные, но не исчезает: порог важности
и правила её не скрывают из-за места, а пользователь видит в карточке, почему она внизу.
Опускаем только региональные события: упоминание страны («Россия и Китай договорились…»)
или региона в новости о другом («у „Яндекса“ атаковали дата-центр в Рязанской области»)
не делает новость чужой.
"""

import re
from dataclasses import dataclass
from typing import Literal

from app.contracts.news import NewsItem, Topic
from app.contracts.profile import CompanyProfile
from app.contracts.rank import Geography, GeoPlace, GeoScope

Source = Literal["home", "counterparty", "group", "related", "person", "market", "trade"]

REGION_NOISE = frozenset(
    {"обл", "облас", "край", "респу", "респ", "автон", "округ", "ао", "г", "город", "федер"}
)
NATIONAL = frozenset({"росси", "рф", "страна"})
COUNTRY_ALIASES = {
    "СОЕДИНЕННЫЕ ШТАТЫ": "США",
    "СОЕДИНЕННЫЕ ШТАТЫ АМЕРИКИ": "США",
    "СОЕДИНЕННОЕ КОРОЛЕВСТВО": "ВЕЛИКОБРИТАНИЯ",
    "БРИТАНИЯ": "ВЕЛИКОБРИТАНИЯ",
    "ОБЪЕДИНЕННЫЕ АРАБСКИЕ ЭМИРАТЫ": "ОАЭ",
    "КНР": "КИТАЙ",
    "РЕСПУБЛИКА КОРЕЯ": "ЮЖНАЯ КОРЕЯ",
    "КОРЕЯ": "ЮЖНАЯ КОРЕЯ",
    "БЕЛОРУССИЯ": "БЕЛАРУСЬ",
    "КЫРГЫЗСТАН": "КИРГИЗИЯ",
    "ТУРЕЦКАЯ РЕСПУБЛИКА": "ТУРЦИЯ",
}
MARKET_KEYS = ("feature:knowledge:markets.sales_regions", "feature:knowledge:markets.regions")
CLIENT_PREFIX = "Со слов клиента: "
SPLIT = re.compile(r"[,;/\n]|\s+и\s+")
SOURCE_TITLES: dict[Source, str] = {
    "home": "регистрация компании",
    "counterparty": "контрагенты",
    "group": "компании группы",
    "related": "связанные компании",
    "person": "связанные лица",
    "market": "рынки со слов клиента",
    "trade": "ВЭД по данным ФТС",
}


@dataclass(frozen=True)
class Place:
    name: str
    source: Source
    feature_id: str | None


def region_key(name: str) -> frozenset[str]:
    words = re.findall(r"[а-яёa-z]+", name.lower().replace("ё", "е"))
    return frozenset(word[:5] for word in words if len(word) >= 2 and word[:5] not in REGION_NOISE)


def same_region(left: str, right: str) -> bool:
    a, b = region_key(left), region_key(right)
    return bool(a and b) and (a <= b or b <= a)


def country(name: str) -> str:
    upper = " ".join(name.upper().replace("Ё", "Е").split())
    return COUNTRY_ALIASES.get(upper, upper)


def national(name: str) -> bool:
    return bool(region_key(name)) and region_key(name) <= NATIONAL


def market_places(profile: CompanyProfile) -> list[Place]:
    """Регионы и страны рынков, которые назвал клиент: «Москва, Татарстан и Казахстан»."""
    places = []
    for feature in profile.features:
        if feature.id not in MARKET_KEYS:
            continue
        text = feature.value if isinstance(feature.value, str) else feature.label
        for part in SPLIT.split(text.removeprefix(CLIENT_PREFIX)):
            if part.strip():
                places.append(Place(part.strip(), "market", feature.id))
    return places


def presence(profile: CompanyProfile) -> list[Place]:
    places: list[Place] = []

    def add(name: str | None, source: Source, feature_id: str | None) -> None:
        if name and name.strip():
            places.append(Place(name.strip(), source, feature_id))

    add(profile.location.region, "home", "feature:region")
    add(profile.location.city, "home", "feature:city")
    for partner in profile.counterparties:
        add(partner.region, "counterparty", f"feature:counterparties_{partner.direction}")
    for member in profile.group.members if profile.group else []:
        add(member.region, "group", "feature:group")
    for company in profile.related_companies:
        add(company.region, "related", "feature:person_links")
    for region in profile.person_regions:
        add(region, "person", "feature:persons")
    places += market_places(profile)
    for name in profile.foreign_trade.countries:
        add(name, "trade", "feature:customs")
    return places


def matches(place: Place, name: str) -> bool:
    return country(place.name) == country(name) or same_region(place.name, name)


def listing(names: list[str]) -> str:
    return ", ".join(f"«{name}»" for name in names)


def locate(news: NewsItem, profile: CompanyProfile, places: list[Place] | None = None) -> Geography:
    places = presence(profile) if places is None else places
    classification = news.classification
    regions = [
        name for name in (classification.regions if classification else []) if not national(name)
    ]
    countries = list(classification.countries) if classification else []
    mentioned = list(dict.fromkeys([*regions, *countries]))
    if not mentioned:
        return Geography(
            scope=GeoScope.NATIONAL, detail="Новость о всей стране или без привязки к месту"
        )
    found = [(place, name) for name in mentioned for place in places if matches(place, name)]
    matched = list(
        dict.fromkeys(
            GeoPlace(name=name, source=place.source, feature_id=place.feature_id)
            for place, name in found
        )
    )
    if any(place.source == "home" for place, _ in found):
        scope = GeoScope.HOME
        detail = f"Новость о регионе регистрации компании: {listing(mentioned)}"
    elif found:
        scope = GeoScope.PRESENCE
        sources = list(dict.fromkeys(SOURCE_TITLES[place.source] for place, _ in found))
        detail = (
            f"Новость о местах присутствия компании ({', '.join(sources)}): {listing(mentioned)}"
        )
    elif regions and classification and Topic.REGIONAL in classification.topics:
        scope = GeoScope.OTHER_REGION
        detail = (
            f"Новость о регионе, где у компании нет ни регистрации, ни контрагентов, ни рынков: "
            f"{listing(regions)}. Карточка опущена в конец ленты"
        )
    else:
        scope = GeoScope.NATIONAL
        detail = (
            f"Упомянуты {listing(mentioned)}, но событие не региональное: место не влияет "
            "на позицию в ленте"
        )
    return Geography(scope=scope, places=mentioned, matched=matched, detail=detail)
