from datetime import UTC, datetime, timedelta
from decimal import Decimal

import asyncpg

from app.contracts.knowledge import KnowledgeBase
from app.contracts.news import Classification, FactStatus, KeyFact, Marker, NewsItem, Topic
from app.news.entities import (
    actor_kind,
    canonical,
    competitor_names,
    entities_of,
    for_profile,
    index,
    mentions,
    overlap,
    profile_entities,
    related,
)
from app.profile.builder import assemble
from app.profile.competitors import apply_competitors
from app.profile.graph import CompanyLinks, LinkedCompany, Payment
from app.profile.legend import parse_legends
from app.profile.row import CompanyRow
from tests.test_data_loader import database
from tests.test_news_classify import news

__all__ = ["database"]

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
CLIENT = CompanyRow(
    company_id="U_1",
    okved_code="46.75",
    okved_name="Торговля оптовая химическими продуктами",
    revenue_rub=Decimal("20000000000"),
    region="г. Москва",
)
LEGENDS = parse_legends(
    """
companies:
  - synthetic_u: U_1
    name: АО «Реагентра»
    competitors:
      - name: Сибур
        url: https://www.wikidata.org/wiki/Q899012
      - name: ПАО «Акрон»
        url: https://www.wikidata.org/wiki/Q4059809
"""
)


def material(
    key: str,
    title: str,
    text: str,
    *,
    entities: list[str] | None = None,
    segments: list[str] | None = None,
    regions: list[str] | None = None,
    who: list[str] | None = None,
    hours: int = 0,
) -> NewsItem:
    item = news(title, text)
    facts = [KeyFact(id="f1", text=text, status=FactStatus.CONFIRMED, who=who)] if who else []
    return item.model_copy(
        update={
            "id": f"news:{key}",
            "published_at": NOW - timedelta(hours=hours),
            "key_facts": facts,
            "classification": Classification(
                topics=[Topic.INDUSTRY],
                markers=[Marker.INDUSTRY_EVENT],
                entities=entities or [],
                segments=segments or [],
                regions=regions or [],
                countries=["Россия", "Китай"],
            ),
        }
    )


def keys(found: object) -> set[str]:
    return {item.key for item in found}  # type: ignore[attr-defined]


def test_canonical_names() -> None:
    assert canonical("ПАО «Сибур Холдинг»") == "сибур холдинг"
    assert canonical("ЦБ РФ") == canonical("Центробанк") == "банк россии"
    assert canonical("Минфин России") == "министерство финансов"
    assert canonical("«Ёлка»") == "елка"


def test_people_and_organizations() -> None:
    assert actor_kind("Эльвира Набиуллина") == "person"
    assert actor_kind("Э. Набиуллина") == "person"
    assert actor_kind("Банк России") == "company"
    assert actor_kind("Минфин") == "company"
    assert actor_kind("«Северсталь»") == "company"
    assert actor_kind("Сибур") == "company"


def test_entities_of_news() -> None:
    item = material(
        "rate",
        "ЦБ сохранил ставку",
        "Совет директоров сохранил ставку.",
        entities=["ЦБ РФ", "Эльвира Набиуллина"],
        segments=["finance"],
        regions=["Республика Татарстан"],
        who=["Банк России"],
    )
    found = keys(entities_of(item))
    assert found == {
        "company:банк россии",
        "person:эльвира набиуллина",
        "industry:finance",
        "region:татарстан",
        "country:китай",
        "topic:industry_event",
    }


def test_mentions_whole_words_only() -> None:
    item = material("x", "Акрон поднял цены", "Акрон и Сибур повысили цены на удобрения.")
    assert keys(mentions(item, ["ПАО «Акрон»", "Сибур", "Ак"])) == {
        "company:акрон",
        "company:сибур",
    }
    assert mentions(material("y", "Акрономия", "Текст"), ["Акрон"]) == []


def profile():  # type: ignore[no-untyped-def]
    links = CompanyLinks(
        payments=[
            Payment(
                partner=LinkedCompany("U_2", okved_code="49.41", region="Москва"),
                direction="outgoing",
                amount_rub=Decimal("100"),
                payments_count=1,
            )
        ]
    )
    plain = assemble(CLIENT, links)
    return apply_competitors(plain, KnowledgeBase(company_id="U_1"), LEGENDS)


def test_profile_entities() -> None:
    built = profile()
    assert competitor_names(built) == ["Сибур", "Акрон"]
    assert keys(profile_entities(built)) >= {
        "industry:chemicals",
        "industry:logistics",
        "region:москва",
        "company:сибур",
        "company:акрон",
    }


def test_overlap_weights() -> None:
    left = entities_of(material("a", "x", "x", entities=["Сибур"], regions=["Москва"]))
    right = entities_of(material("b", "y", "y", entities=["Сибур"], regions=["Казань"]))
    score, shared = overlap(left, right)
    assert "company:сибур" in shared and score >= 3.0


async def test_related_and_profile_news(database: asyncpg.Connection) -> None:
    items = [
        material("sibur-1", "Сибур запустил завод", "Сибур запустил завод.", entities=["Сибур"]),
        material(
            "sibur-2", "Сибур поднял цены", "Цены выросли.", entities=["ПАО «Сибур»"], hours=5
        ),
        material("mention", "Цены на пластик", "Акрон и другие производители.", hours=2),
        material("other", "Урожай зерна", "Аграрии собрали урожай.", segments=["agro"]),
        material("old", "Сибур год назад", "Старая новость.", entities=["Сибур"], hours=24 * 40),
    ]
    await database.execute(
        "CREATE TABLE news_items (news_id text, published_at timestamptz, item jsonb)"
    )
    for item in items:
        await database.execute(
            "INSERT INTO news_items VALUES ($1, $2, $3::jsonb)",
            item.id,
            item.published_at,
            item.model_dump_json(),
        )
    assert await index(database, NOW - timedelta(days=60)) == 5
    links = await related(database, "news:sibur-1", NOW)
    assert [link.news_id for link in links] == ["news:sibur-2"]
    assert links[0].shared == ["company:сибур", "country:китай"]
    assert await related(database, "news:other", NOW) == []

    found = await for_profile(database, profile(), NOW - timedelta(days=7))
    by_id = {link.news_id: link for link in found}
    assert set(by_id) == {"news:sibur-1", "news:sibur-2", "news:mention"}
    assert by_id["news:mention"].shared == ["company:акрон"]
