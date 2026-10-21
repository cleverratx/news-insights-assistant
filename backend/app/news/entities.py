"""Сущности новости (A3-11): компании, отрасли, регионы, персоны и темы с общим ключом
вида «company:сибур», «industry:chemicals», «region:татарстан». По ключам новости
связываются между собой (продолжение сюжета, другие события той же компании) и с профилем
клиента: его отраслью, регионами, отраслями контрагентов и конкурентами.

Сущности собираются из того, что уже есть в разборе, без новых вызовов модели:
участники из классификации и ключевых фактов, сегменты рынка, регионы, страны, маркеры.
Чтобы «ЦБ», «ЦБ РФ» и «Банк России» стали одним ключом, названия приводятся к одному виду:
без кавычек и организационно-правовой формы, в нижнем регистре, «ё» → «е», и через словарь
синонимов частых ведомств. Названия конкурентов из профиля дополнительно ищутся в тексте:
так новость о конкуренте находится, даже если её разбирали по словарю, без модели.
"""

import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from app.contracts.news import NewsItem, Topic
from app.contracts.profile import CompanyProfile
from app.news.markers import markers_of

EntityKind = Literal["company", "person", "industry", "region", "country", "topic"]
WEIGHTS: dict[EntityKind, float] = {
    "company": 3.0,
    "person": 2.0,
    "industry": 1.0,
    "region": 1.0,
    "country": 0.5,
    "topic": 0.3,
}
RELATED_WINDOW = timedelta(days=14)
QUOTES = re.compile(r"[«»\"“”„']")
ORG_FORMS = re.compile(
    r"^(ооо|ао|пао|зао|оао|нао|ип|гк|группа компаний|компания|концерн|холдинг)\s+",
    re.IGNORECASE,
)
REGION_WORDS = re.compile(r"\b(г\.|город|республика|респ\.)\s*", re.IGNORECASE)
PERSON = re.compile(
    r"^(?:[А-ЯЁ]\.\s?){1,2}[А-ЯЁ][а-яё]+$" r"|^[А-ЯЁ][а-яё]+(?:\s[А-ЯЁ][а-яё]+){1,2}$"
)
ORGANIZATION_HINT = re.compile(
    r"(банк|министерств|минфин|минэк|минпром|служба|агентств|фонд|биржа|правительств|"
    r"госдума|совет|центр|компани|групп|завод|комбинат|холдинг|корпорац|ржд|сбер)",
    re.IGNORECASE,
)
ALIASES = {
    "цб": "банк россии",
    "цб рф": "банк россии",
    "центробанк": "банк россии",
    "центральный банк": "банк россии",
    "центральный банк рф": "банк россии",
    "центральный банк российской федерации": "банк россии",
    "регулятор": "банк россии",
    "минфин": "министерство финансов",
    "минфин россии": "министерство финансов",
    "минфин рф": "министерство финансов",
    "минэкономразвития": "министерство экономического развития",
    "минпромторг": "министерство промышленности и торговли",
    "фнс": "федеральная налоговая служба",
    "росстат": "федеральная служба государственной статистики",
    "мосбиржа": "московская биржа",
    "ржд": "российские железные дороги",
    "оао ржд": "российские железные дороги",
    "сбербанк": "сбер",
    "рф": "россия",
    "российская федерация": "россия",
}


@dataclass(frozen=True)
class Entity:
    kind: EntityKind
    key: str
    name: str

    @property
    def weight(self) -> float:
        return WEIGHTS[self.kind]


def canonical(name: str) -> str:
    """Название как ключ: без кавычек, ООО и регистра, синонимы — к одному имени."""
    value = QUOTES.sub("", name).strip().lower().replace("ё", "е")
    value = ORG_FORMS.sub("", value).strip()
    value = re.sub(r"\s+", " ", value)
    return ALIASES.get(value, value)


def region_key(name: str) -> str:
    return re.sub(r"\s+", " ", REGION_WORDS.sub("", name).strip().lower().replace("ё", "е"))


def entity(kind: EntityKind, name: str) -> Entity | None:
    if kind == "region":
        key = region_key(name)
    elif kind in ("industry", "topic"):
        key = name.strip().lower()
    else:
        key = canonical(name)
    if len(key) < 2:
        return None
    return Entity(kind, f"{kind}:{key}", name.strip())


def actor_kind(name: str) -> EntityKind:
    """Участник — человек или организация. «Э. Набиуллина», «Антон Силуанов» — человек;
    «Банк России», «Минфин» и всё в кавычках или с ООО — организация."""
    stripped = name.strip()
    if QUOTES.search(stripped) or ORGANIZATION_HINT.search(stripped):
        return "company"
    if canonical(stripped) in ALIASES.values():
        return "company"
    return "person" if PERSON.match(stripped) else "company"


def entities_of(item: NewsItem) -> list[Entity]:
    """Сущности новости, без повторов по ключу."""
    found: list[Entity | None] = []
    classification = item.classification
    actors: list[str] = []
    if classification is not None:
        actors += classification.entities
        found += [entity("industry", segment) for segment in classification.segments]
        found += [entity("region", region) for region in classification.regions]
        found += [entity("country", country) for country in classification.countries]
    for fact in item.key_facts:
        actors += fact.who
    found += [entity(actor_kind(actor), actor) for actor in actors]
    found += [entity("topic", marker.value) for marker in markers_of(item)]
    unique: dict[str, Entity] = {}
    for candidate in found:
        if candidate is not None and candidate.key != "country:россия":
            unique.setdefault(candidate.key, candidate)
    return list(unique.values())


def mentions(item: NewsItem, names: Iterable[str]) -> list[Entity]:
    """Компании из списка, которые упомянуты в заголовке или тексте: поиск по слову целиком,
    без учёта регистра. Для конкурентов и других имён, которые знает профиль."""
    text = f" {item.title} {item.text} ".lower().replace("ё", "е")
    found = []
    for name in names:
        key = canonical(name)
        if len(key) >= 3 and re.search(rf"(?<!\w){re.escape(key)}(?!\w)", text):
            candidate = entity("company", name)
            if candidate is not None:
                found.append(candidate)
    return found


def competitor_names(profile: CompanyProfile) -> list[str]:
    """Названия конкурентов: ключевые слова темы company, которую приносят конкуренты (A2-17)."""
    names: list[str] = []
    for exposure in profile.exposures:
        if exposure.topic is Topic.COMPANY and "feature:competitors" in exposure.feature_ids:
            names += exposure.keywords
    return list(dict.fromkeys(names))


def profile_entities(profile: CompanyProfile) -> list[Entity]:
    """Сущности, по которым новость касается клиента: его отрасль и регионы, отрасли
    контрагентов и группы, конкуренты."""
    found: list[Entity | None] = []
    if profile.industry.segment != "other":
        found.append(entity("industry", profile.industry.segment))
    found += [entity("industry", item.segment) for item in profile.counterparties if item.segment]
    if profile.group is not None:
        found += [
            entity("industry", item.segment) for item in profile.group.members if item.segment
        ]
    regions = [profile.location.region, *profile.person_regions]
    found += [entity("region", region) for region in regions if region]
    found += [entity("company", name) for name in competitor_names(profile)]
    unique: dict[str, Entity] = {}
    for candidate in found:
        if candidate is not None:
            unique.setdefault(candidate.key, candidate)
    return list(unique.values())


@dataclass(frozen=True)
class Link:
    news_id: str
    score: float
    shared: list[str]


def overlap(left: Sequence[Entity], right: Sequence[Entity]) -> tuple[float, list[str]]:
    """Сила связи — сумма весов общих сущностей: общая компания весит больше общей темы."""
    keys = {item.key: item for item in left}
    shared = [item for item in right if item.key in keys]
    return sum(item.weight for item in shared), [item.key for item in shared]


SCHEMA = """
CREATE TABLE IF NOT EXISTS news_entities (
    news_id text NOT NULL,
    key text NOT NULL,
    kind text NOT NULL,
    name text NOT NULL,
    published_at timestamptz NOT NULL,
    PRIMARY KEY (news_id, key)
);
CREATE INDEX IF NOT EXISTS news_entities_key ON news_entities (key, published_at DESC);
"""
ITEMS_SQL = "SELECT news_id, published_at, item FROM news_items WHERE published_at >= $1"
CLEAR_SQL = "DELETE FROM news_entities WHERE news_id = $1"
INSERT_SQL = """
INSERT INTO news_entities (news_id, key, kind, name, published_at) VALUES ($1, $2, $3, $4, $5)
ON CONFLICT (news_id, key) DO NOTHING
"""
OWN_SQL = "SELECT key, kind, name FROM news_entities WHERE news_id = $1"
RELATED_SQL = """
SELECT news_id, key, kind FROM news_entities
WHERE key = ANY($1::text[]) AND news_id <> $2 AND published_at BETWEEN $3 AND $4
"""
BY_KEYS_SQL = """
SELECT news_id, key, kind FROM news_entities
WHERE key = ANY($1::text[]) AND published_at >= $2
"""


async def ensure_schema(connection: Any) -> None:
    await connection.execute(SCHEMA)


async def index(connection: Any, since: datetime) -> int:
    """Пересобирает сущности новостей начиная с since: разбор мог обновиться."""
    await ensure_schema(connection)
    count = 0
    for record in await connection.fetch(ITEMS_SQL, since):
        item = NewsItem.model_validate_json(record["item"])
        await connection.execute(CLEAR_SQL, record["news_id"])
        for found in entities_of(item):
            await connection.execute(
                INSERT_SQL,
                record["news_id"],
                found.key,
                found.kind,
                found.name,
                record["published_at"],
            )
        count += 1
    return count


def ranked(records: Iterable[Any]) -> list[Link]:
    scores: defaultdict[str, float] = defaultdict(float)
    shared: dict[str, list[str]] = {}
    for record in records:
        scores[record["news_id"]] += WEIGHTS[record["kind"]]
        shared.setdefault(record["news_id"], []).append(record["key"])
    order = sorted(scores, key=lambda news_id: (-scores[news_id], news_id))
    return [Link(news_id, round(scores[news_id], 3), sorted(shared[news_id])) for news_id in order]


async def related(
    connection: Any, news_id: str, published_at: datetime, limit: int = 10, min_score: float = 2.0
) -> list[Link]:
    """Новости, связанные с данной общими сущностями в окне ±14 дней: сначала с общей
    компанией или персоной. Одна общая тема или отрасль связью не считается."""
    own = await connection.fetch(OWN_SQL, news_id)
    keys = [record["key"] for record in own if record["kind"] != "topic"]
    if not keys:
        return []
    records = await connection.fetch(
        RELATED_SQL, keys, news_id, published_at - RELATED_WINDOW, published_at + RELATED_WINDOW
    )
    return [link for link in ranked(records) if link.score >= min_score][:limit]


async def for_profile(
    connection: Any, profile: CompanyProfile, since: datetime, limit: int = 50
) -> list[Link]:
    """Новости, которые касаются клиента по сущностям профиля, с тем, что совпало. Названия
    конкурентов дополнительно ищутся в тексте новостей — на случай разбора без модели."""
    wanted = profile_entities(profile)
    records = list(await connection.fetch(BY_KEYS_SQL, [item.key for item in wanted], since))
    names = competitor_names(profile)
    if names:
        for record in await connection.fetch(ITEMS_SQL, since):
            item = NewsItem.model_validate_json(record["item"])
            for found in mentions(item, names):
                records.append({"news_id": record["news_id"], "key": found.key, "kind": "company"})
    unique = {(record["news_id"], record["key"]): record for record in records}
    return ranked(unique.values())[:limit]
