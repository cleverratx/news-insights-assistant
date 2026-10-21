"""Конкуренты (A2-17): кто работает на одном рынке с клиентом.

Три источника, у каждого свой смысл:

- bank_data — компании из выгрузки с тем же видом деятельности, сопоставимой выручкой
  (в пределах трёх раз) и, лучше, в том же регионе. Они обезличены, поэтому в поиск новостей
  не идут, зато показывают, с кем клиент делит рынок в базе банка. Контрагенты и компании
  группы не конкуренты: их видно в связанных компаниях.
- open_source — реальные игроки отрасли из открытых источников, для демо-легенды. Ищем сами
  запросом к Wikidata по отраслям сегмента (scripts/find_competitors.py), результат
  проверяется руками и ложится в legends.yaml. Во время работы сервиса сеть не нужна.
- client — компании, которых назвал клиент.

Список правит клиент: добавленный конкурент — факт базы знаний `competitors.<id>`
со значением true, исключённый — тот же ключ со значением false. История правок остаётся.
Названия реальных конкурентов (не обезличенных) становятся темой company в профиле:
отбор найдёт новости о них.
"""

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import uuid4

import httpx
from pydantic import HttpUrl

from app.contracts.competitors import Competitor, Competitors, CompetitorSource
from app.contracts.knowledge import FactCategory, FactSource, KnowledgeBase, KnowledgeFact
from app.contracts.news import Topic
from app.contracts.profile import (
    CompanyProfile,
    Exposure,
    ExposureDirection,
    FeatureKind,
    ProfileFeature,
)
from app.profile.builder import COMPANY_SQL
from app.profile.features import city_name, money
from app.profile.knowledge import COMPETITORS_PREFIX, new_fact, profile_with_knowledge, revise
from app.profile.legend import CompetitorEntry, Legends, get_legends
from app.profile.okved import resolve
from app.profile.row import CompanyRow

TOP_BANK = 10
REVENUE_RATIO = 3
POOL = 200
KEY_PREFIX = COMPETITORS_PREFIX
CLIENT_PREFIX = "client:"
WIKIDATA_PREFIX = "wikidata:"
WEIGHTS = {"code": 0.5, "group": 0.4, "class": 0.25, "revenue": 0.3, "region": 0.2}
ORG_FORMS = re.compile(r"^(ООО|АО|ПАО|ЗАО|ОАО|ИП|ГК|НАО)\s+", re.IGNORECASE)
QUOTES = re.compile(r"[«»\"“”„]")
DISAMBIGUATION = re.compile(r"\s*\([^)]*\)$")
REGION_NOISE = re.compile(r"\b(г\.|город|область|обл\.|край|республика|респ\.)\s*", re.IGNORECASE)

CANDIDATES_SQL = """
SELECT company_id, okved_code, okved_name, region, revenue_rub
FROM companies c
WHERE c.company_id <> $1
  AND split_part(c.okved_code, '.', 1) = $2
  AND c.revenue_rub BETWEEN $3 AND $4
  AND ($5::text IS NULL OR c.group_id IS DISTINCT FROM $5)
  AND NOT EXISTS (
      SELECT 1 FROM payments p
      WHERE (p.payer_id = $1 AND p.beneficiary_id = c.company_id)
         OR (p.beneficiary_id = $1 AND p.payer_id = c.company_id)
  )
ORDER BY (c.okved_code = $6) DESC, abs(ln(c.revenue_rub / $7)), c.company_id
LIMIT $8
"""

# Отрасли Wikidata (свойство P452) для сегментов справочника ОКВЭД. Идентификаторы взяты из
# отраслей российских компаний в Wikidata, по одной-несколько на сегмент; сегмента нет —
# открытый поиск для него не делаем.
WIKIDATA_INDUSTRIES: dict[str, tuple[str, ...]] = {
    "agro": ("Q11451", "Q48803"),
    "oil_gas": ("Q862571", "Q1349660"),
    "mining_metals": ("Q2285982", "Q1048894", "Q1924906", "Q11467", "Q44497"),
    "food": ("Q540912", "Q13554450", "Q955043", "Q11644505", "Q213376"),
    "consumer_goods": ("Q770899", "Q607081", "Q11828862", "Q12752882"),
    "chemicals": ("Q207652",),
    "healthcare": ("Q507443",),
    "construction": ("Q385378", "Q3875186", "Q13405640", "Q695829"),
    "machinery": ("Q101333", "Q1957908", "Q21652039", "Q474200", "Q20091852"),
    "electronics": ("Q5358497", "Q1326885", "Q11650"),
    "utilities": ("Q1803786", "Q2151621", "Q383973"),
    "real_estate": ("Q1660132", "Q695829"),
    "auto": ("Q190117",),
    "trade": ("Q220695", "Q601401"),
    "retail": ("Q126793", "Q484847"),
    "logistics": ("Q177777", "Q3565868"),
    "hospitality": ("Q41958", "Q49389"),
    "it_telecom": ("Q880371", "Q11661", "Q2401742", "Q418"),
    "media": ("Q11033", "Q15078788"),
    "finance": ("Q837171", "Q22687", "Q43183"),
}
WIKIDATA_URL = "https://query.wikidata.org/sparql"
WIKIDATA_QUERY = """
SELECT ?company ?companyLabel ?industryLabel ?hqLabel ?regionLabel ?links WHERE {{
  VALUES ?industry {{ {industries} }}
  ?company wdt:P452 ?industry; wdt:P17 wd:Q159; wikibase:sitelinks ?links.
  FILTER NOT EXISTS {{ ?company wdt:P576 [] }}
  OPTIONAL {{ ?company wdt:P159 ?hq. OPTIONAL {{ ?hq wdt:P131 ?region. }} }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "ru,en". }}
}}
ORDER BY DESC(?links)
LIMIT {limit}
"""
USER_AGENT = "news-insights-assistant/0.1 (Alfa Future Hackathon 2026)"


@dataclass(frozen=True)
class Candidate:
    company_id: str
    okved_code: str | None
    okved_name: str | None
    region: str | None
    revenue_rub: Decimal


@dataclass(frozen=True)
class OpenCompetitor:
    """Компания из открытого источника, как её вернул поиск."""

    qid: str
    name: str
    industry: str | None
    region: str | None
    same_region: bool

    @property
    def url(self) -> str:
        return f"https://www.wikidata.org/wiki/{self.qid}"


def region_key(value: str | None) -> str:
    return REGION_NOISE.sub("", city_name(value) or "").strip().lower()


def same_region(left: str | None, right: str | None) -> bool:
    return bool(region_key(left)) and region_key(left) == region_key(right)


def code_match(own: str, other: str | None) -> str | None:
    """Насколько близок вид деятельности: тот же код, та же группа (две части кода), класс."""
    if not other:
        return None
    if other == own:
        return "code"
    if other.split(".")[:2] == own.split(".")[:2] and len(own.split(".")) > 1:
        return "group"
    return "class" if other.split(".")[0] == own.split(".")[0] else None


def bank_competitor(row: CompanyRow, candidate: Candidate) -> Competitor | None:
    if not row.okved_code or not row.revenue_rub:
        return None
    match = code_match(row.okved_code, candidate.okved_code)
    if match is None:
        return None
    ratio = abs(math.log(float(candidate.revenue_rub / row.revenue_rub)))
    closeness = max(0.0, 1 - ratio / math.log(REVENUE_RATIO))
    region = same_region(row.region, candidate.region)
    score = WEIGHTS[match] + WEIGHTS["revenue"] * closeness + (WEIGHTS["region"] if region else 0)
    activity = {
        "code": f"Тот же вид деятельности: ОКВЭД {candidate.okved_code}",
        "group": f"Близкий вид деятельности: ОКВЭД {candidate.okved_code}",
        "class": f"Тот же класс ОКВЭД {row.okved_code.split('.')[0]}: {candidate.okved_code}",
    }[match]
    reasons = [
        activity,
        f"Выручка {money(candidate.revenue_rub)} — сопоставима с вашей ({money(row.revenue_rub)})",
    ]
    if region:
        reasons.append(f"Тот же регион: {candidate.region}")
    segment = resolve(candidate.okved_code, candidate.okved_name).segment.id
    return Competitor(
        id=candidate.company_id,
        source=CompetitorSource.BANK_DATA,
        company_id=candidate.company_id,
        industry=candidate.okved_name,
        segment=None if segment == "other" else segment,
        region=candidate.region,
        revenue_rub=float(candidate.revenue_rub),
        score=round(min(1.0, score), 4),
        reasons=reasons,
    )


def legend_id(entry: CompetitorEntry) -> str:
    match = re.search(r"(Q\d+)$", entry.url or "")
    if match:
        return f"{WIKIDATA_PREFIX}{match.group(1)}"
    slug = re.sub(r"\W+", "-", QUOTES.sub("", entry.name).lower()).strip("-")
    return f"legend:{slug or uuid4().hex[:8]}"


def open_competitor(row: CompanyRow, entry: CompetitorEntry) -> Competitor:
    reasons = ["Реальный игрок вашего рынка по открытым источникам"]
    if same_region(row.region, entry.region):
        reasons.append(f"Тот же регион: {entry.region}")
    return Competitor(
        id=legend_id(entry),
        source=CompetitorSource.OPEN_SOURCE,
        name=entry.name.strip(),
        industry=entry.industry,
        region=entry.region,
        reasons=reasons,
        url=HttpUrl(entry.url) if entry.url else None,
    )


def competitor_key(competitor_id: str) -> str:
    return f"{KEY_PREFIX}{competitor_id}"


def client_facts(base: KnowledgeBase) -> dict[str, KnowledgeFact]:
    """Решения клиента о конкурентах: id конкурента → факт (true — конкурент, false — нет)."""
    return {
        fact.key.removeprefix(KEY_PREFIX): fact
        for fact in base.facts
        if fact.key
        and fact.key.startswith(KEY_PREFIX)
        and fact.source is FactSource.CLIENT
        and isinstance(fact.value, bool)
    }


def client_competitor(competitor_id: str, fact: KnowledgeFact) -> Competitor:
    return Competitor(
        id=competitor_id,
        source=CompetitorSource.CLIENT,
        name=fact.text,
        reasons=["Назвал клиент"],
        fact_id=fact.id,
    )


def build(
    row: CompanyRow,
    candidates: Sequence[Candidate],
    legend: Sequence[CompetitorEntry],
    base: KnowledgeBase,
) -> Competitors:
    """Список конкурентов: названные клиентом, реальные игроки рынка, затем похожие компании
    выгрузки по убыванию сходства. Исключённые клиентом — отдельно, чтобы их можно было вернуть."""
    decisions = client_facts(base)
    bank = [item for item in (bank_competitor(row, c) for c in candidates) if item is not None]
    bank = sorted(bank, key=lambda item: (-(item.score or 0), item.id))[:TOP_BANK]
    found: dict[str, Competitor] = {}
    for item in [*(open_competitor(row, entry) for entry in legend), *bank]:
        found.setdefault(item.id, item)
    added = {
        competitor_id: client_competitor(competitor_id, fact)
        for competitor_id, fact in decisions.items()
        if fact.value is True and competitor_id not in found
    }
    competitors: list[Competitor] = [*added.values()]
    excluded: list[Competitor] = []
    for competitor_id, item in found.items():
        fact = decisions.get(competitor_id)
        if fact is not None and fact.value is False:
            excluded.append(item.model_copy(update={"fact_id": fact.id}))
        else:
            competitors.append(item)
    warnings = []
    if not row.okved_code:
        warnings.append("Код ОКВЭД пустой: конкурентов в базе банка не подобрать")
    elif not row.revenue_rub:
        warnings.append("Выручки нет: сопоставимых по размеру компаний не подобрать")
    return Competitors(
        company_id=row.company_id, competitors=competitors, excluded=excluded, warnings=warnings
    )


def add_fact(base: KnowledgeBase, name: str) -> KnowledgeFact:
    competitor_id = f"{CLIENT_PREFIX}{uuid4().hex[:12]}"
    return new_fact(
        base.company_id,
        FactCategory.MARKETS,
        name.strip(),
        FactSource.CLIENT,
        key=competitor_key(competitor_id),
        value=True,
    )


def exclude_fact(base: KnowledgeBase, competitor: Competitor) -> KnowledgeFact:
    """Клиент убрал кандидата. Если по ключу уже был факт (клиент возвращал кандидата раньше),
    это его новая версия."""
    key = competitor_key(competitor.id)
    text = f"Не конкурент: {competitor.name or competitor.id}"
    current = next((fact for fact in base.facts if fact.key == key), None)
    if current is not None:
        return revise(current, source=FactSource.CLIENT, text=text, value=False)
    return new_fact(
        base.company_id, FactCategory.MARKETS, text, FactSource.CLIENT, key=key, value=False
    )


def search_name(name: str) -> str:
    """Как искать компанию в новостях: без кавычек и организационно-правовой формы."""
    return ORG_FORMS.sub("", QUOTES.sub("", name)).strip()


def apply_competitors(
    profile: CompanyProfile, base: KnowledgeBase, legends: Legends
) -> CompanyProfile:
    """Профиль с конкурентами, у которых есть настоящее название: из открытых источников
    и от клиента, кроме исключённых. Они становятся темой company: отбор найдёт новости о них."""
    row = CompanyRow(company_id=profile.company_id, region=profile.location.region)
    named = [
        item
        for item in build(
            row, [], legends.competitors.get(profile.company_id, []), base
        ).competitors
        if item.name
    ]
    if not named:
        return profile
    names = [item.name for item in named if item.name]
    feature = ProfileFeature(
        id="feature:competitors",
        kind=FeatureKind.COMPETITOR,
        label=f"Конкуренты на вашем рынке: {', '.join(names)}",
        value=len(names),
        source_fields=[f"competitors:{item.id}" for item in named],
    )
    exposure = Exposure(
        topic=Topic.COMPANY,
        direction=ExposureDirection.MIXED,
        reason="Вы работаете на одном рынке: сделки, цены, проблемы и планы конкурентов "
        "меняют расклад сил, а значит, ваши риски и возможности",
        feature_ids=[feature.id],
        keywords=list(dict.fromkeys(search_name(name) for name in names)),
    )
    return profile.model_copy(
        update={
            "features": [*profile.features, feature],
            "exposures": [*profile.exposures, exposure],
        }
    )


async def fetch_candidates(connection: Any, row: CompanyRow) -> list[Candidate]:
    if not row.okved_code or not row.revenue_rub:
        return []
    revenue = row.revenue_rub
    records = await connection.fetch(
        CANDIDATES_SQL,
        row.company_id,
        row.okved_code.split(".")[0],
        revenue / REVENUE_RATIO,
        revenue * REVENUE_RATIO,
        row.group_id,
        row.okved_code,
        revenue,
        POOL,
    )
    return [
        Candidate(
            company_id=record["company_id"],
            okved_code=record["okved_code"],
            okved_name=record["okved_name"],
            region=record["region"],
            revenue_rub=record["revenue_rub"],
        )
        for record in records
    ]


async def competitors_for(
    connection: Any, company_id: str, legends: Legends | None = None
) -> Competitors | None:
    """Конкуренты компании с учётом правок клиента. None — компании нет."""
    loaded = await profile_with_knowledge(connection, company_id)
    if loaded is None:
        return None
    row = CompanyRow.from_record(await connection.fetchrow(COMPANY_SQL, company_id))
    legend = (get_legends() if legends is None else legends).competitors.get(company_id, [])
    return build(row, await fetch_candidates(connection, row), legend, loaded[1])


def find(competitors: Competitors, competitor_id: str) -> Competitor | None:
    return next(
        (
            item
            for item in [*competitors.competitors, *competitors.excluded]
            if item.id == competitor_id
        ),
        None,
    )


# Поиск по открытым источникам для демо-легенды.


def wikidata_query(segment: str, limit: int) -> str | None:
    industries = WIKIDATA_INDUSTRIES.get(segment)
    if not industries:
        return None
    values = " ".join(f"wd:{qid}" for qid in industries)
    return WIKIDATA_QUERY.format(industries=values, limit=limit)


def parse_wikidata(payload: dict[str, Any], region: str | None) -> list[OpenCompetitor]:
    """Ответ SPARQL — по строке на сочетание штаб-квартиры и региона: склеиваем по компании.
    Порядок — как в ответе, по известности (числу статей в Википедии)."""
    found: dict[str, OpenCompetitor] = {}
    for binding in payload.get("results", {}).get("bindings", []):
        qid = binding["company"]["value"].rsplit("/", 1)[-1]
        name = DISAMBIGUATION.sub("", binding.get("companyLabel", {}).get("value", ""))
        if not name or name == qid:
            continue
        places = [binding.get(key, {}).get("value") for key in ("hqLabel", "regionLabel")]
        local = any(same_region(region, place) for place in places)
        known = found.get(qid)
        if known is not None and (known.same_region or not local):
            continue
        found[qid] = OpenCompetitor(
            qid=qid,
            name=name,
            industry=binding.get("industryLabel", {}).get("value"),
            region=next((place for place in places if place), None),
            same_region=local,
        )
    return list(found.values())


async def search_wikidata(
    client: httpx.AsyncClient, segment: str, region: str | None, limit: int = 15
) -> list[OpenCompetitor]:
    """Реальные компании России из отраслей сегмента. Пустой список — сегмент не сопоставлен."""
    query = wikidata_query(segment, limit * 4)
    if query is None:
        return []
    response = await client.get(
        WIKIDATA_URL,
        params={"query": query},
        headers={"Accept": "application/sparql-results+json", "User-Agent": USER_AGENT},
        timeout=60,
    )
    response.raise_for_status()
    return parse_wikidata(response.json(), region)[:limit]
