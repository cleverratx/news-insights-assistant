import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from app.contracts.competitors import Competitor, CompetitorSource
from app.contracts.knowledge import FactCategory, FactSource, KnowledgeBase, KnowledgeFact
from app.contracts.news import Topic
from app.contracts.profile import FeatureKind
from app.profile.builder import assemble
from app.profile.competitors import (
    TOP_BANK,
    Candidate,
    apply_competitors,
    bank_competitor,
    build,
    competitor_key,
    exclude_fact,
    parse_wikidata,
    search_name,
    search_wikidata,
    wikidata_query,
)
from app.profile.graph import CompanyLinks
from app.profile.knowledge import apply_knowledge, new_fact, resolve
from app.profile.legend import load_legends, parse_legends
from app.profile.row import CompanyRow

NOW = datetime(2026, 10, 21, tzinfo=UTC)
CLIENT = CompanyRow(
    company_id="U_1",
    okved_code="46.75",
    okved_name="Торговля оптовая химическими продуктами",
    revenue_rub=Decimal("20000000000"),
    region="Москва",
)
LEGENDS = parse_legends(
    """
companies:
  - synthetic_u: U_1
    name: АО «Реагентра»
    competitors:
      - name: Сибур
        url: https://www.wikidata.org/wiki/Q899012
        region: Москва
        industry: химическая промышленность
      - name: ПАО «Акрон»
        url: https://www.wikidata.org/wiki/Q4059809
      - name: " "
"""
)
LEGEND = LEGENDS.competitors["U_1"]


def candidate(company_id: str, code: str, revenue: str, region: str | None = "Москва") -> Candidate:
    return Candidate(
        company_id=company_id,
        okved_code=code,
        okved_name=None,
        region=region,
        revenue_rub=Decimal(revenue),
    )


def base(*facts: KnowledgeFact) -> KnowledgeBase:
    return resolve("U_1", facts)


def decision(competitor_id: str, text: str, value: bool) -> KnowledgeFact:
    return new_fact(
        "U_1",
        FactCategory.MARKETS,
        text,
        FactSource.CLIENT,
        key=competitor_key(competitor_id),
        value=value,
        now=NOW,
    )


def test_bank_score_by_activity_revenue_and_region() -> None:
    twin = bank_competitor(CLIENT, candidate("U_2", "46.75", "20000000000"))
    assert twin is not None
    assert twin.score == 1.0
    assert twin.reasons == [
        "Тот же вид деятельности: ОКВЭД 46.75",
        "Выручка 20 млрд ₽ — сопоставима с вашей (20 млрд ₽)",
        "Тот же регион: Москва",
    ]
    assert twin.segment == "chemicals"
    far = bank_competitor(CLIENT, candidate("U_3", "46.90", "60000000000", "Татарстан"))
    assert far is not None and far.score == 0.25
    assert far.reasons[0] == "Тот же класс ОКВЭД 46: 46.90"
    near = bank_competitor(CLIENT, candidate("U_4", "46.75.1", "10000000000", "г. Москва"))
    assert near is not None and near.reasons[0].startswith("Близкий вид деятельности")
    assert near.score is not None and 0.6 < near.score < 0.8
    assert bank_competitor(CLIENT, candidate("U_5", "20.13", "20000000000")) is None
    assert (
        bank_competitor(CLIENT.model_copy(update={"revenue_rub": None}), twin_candidate()) is None
    )


def twin_candidate() -> Candidate:
    return candidate("U_2", "46.75", "20000000000")


def test_build_orders_and_limits() -> None:
    candidates = [
        candidate(f"U_{100 + index}", "46.75", str(20_000_000_000 + index * 10**9))
        for index in range(15)
    ]
    added = decision("client:abc", "ООО «Химснаб»", True)
    result = build(CLIENT, candidates, LEGEND, base(added))
    ids = [item.id for item in result.competitors]
    assert ids[:3] == ["client:abc", "wikidata:Q899012", "wikidata:Q4059809"]
    bank = [item for item in result.competitors if item.source is CompetitorSource.BANK_DATA]
    assert len(bank) == TOP_BANK
    assert bank[0].id == "U_100"
    assert result.competitors[0].fact_id == added.id
    assert result.competitors[1].reasons == [
        "Реальный игрок вашего рынка по открытым источникам",
        "Тот же регион: Москва",
    ]
    assert str(result.competitors[1].url) == "https://www.wikidata.org/wiki/Q899012"


def test_client_excludes_candidate() -> None:
    knowledge = base()
    found = build(CLIENT, [twin_candidate()], LEGEND, knowledge)
    sibur = next(item for item in found.competitors if item.id == "wikidata:Q899012")
    fact = exclude_fact(knowledge, sibur)
    assert (fact.key, fact.value, fact.text) == (
        "competitors.wikidata:Q899012",
        False,
        "Не конкурент: Сибур",
    )
    result = build(CLIENT, [twin_candidate()], LEGEND, base(fact))
    assert "wikidata:Q899012" not in [item.id for item in result.competitors]
    assert [item.id for item in result.excluded] == ["wikidata:Q899012"]
    assert result.excluded[0].fact_id == fact.id


def test_warnings_without_okved_or_revenue() -> None:
    no_code = build(CLIENT.model_copy(update={"okved_code": None}), [], [], base())
    assert no_code.warnings == ["Код ОКВЭД пустой: конкурентов в базе банка не подобрать"]
    no_revenue = build(CLIENT.model_copy(update={"revenue_rub": None}), [], [], base())
    assert no_revenue.warnings == ["Выручки нет: сопоставимых по размеру компаний не подобрать"]


def test_named_competitors_go_to_news_search() -> None:
    profile = assemble(CLIENT, CompanyLinks())
    knowledge = base(
        decision("client:abc", "ООО «Химснаб»", True),
        decision("wikidata:Q899012", "Не конкурент: Сибур", False),
    )
    updated = apply_competitors(apply_knowledge(profile, knowledge), knowledge, LEGENDS)
    feature = updated.features[-1]
    assert feature.kind is FeatureKind.COMPETITOR
    assert feature.label == "Конкуренты на вашем рынке: ООО «Химснаб», ПАО «Акрон»"
    assert feature.source_fields == ["competitors:client:abc", "competitors:wikidata:Q4059809"]
    exposure = updated.exposures[-1]
    assert exposure.topic is Topic.COMPANY
    assert exposure.keywords == ["Химснаб", "Акрон"]
    assert exposure.feature_ids == [feature.id]
    assert not [item for item in updated.features if item.kind is FeatureKind.KNOWLEDGE]


def test_no_named_competitors_keeps_profile() -> None:
    profile = assemble(CLIENT, CompanyLinks())
    assert apply_competitors(profile, base(), parse_legends("companies: []")) is profile


def test_search_name() -> None:
    assert search_name("ПАО «Акрон»") == "Акрон"
    assert search_name('ООО "Деловые линии"') == "Деловые линии"
    assert search_name("Сибур") == "Сибур"


def binding(qid: str, name: str, hq: str | None = None, region: str | None = None) -> Any:
    row: dict[str, Any] = {
        "company": {"value": f"http://www.wikidata.org/entity/{qid}"},
        "companyLabel": {"value": name},
        "industryLabel": {"value": "химическая промышленность"},
    }
    if hq:
        row["hqLabel"] = {"value": hq}
    if region:
        row["regionLabel"] = {"value": region}
    return row


PAYLOAD = {
    "results": {
        "bindings": [
            binding("Q1", "Уралкалий", "Березники", "Пермский край"),
            binding("Q2", "Башкирская содовая компания", "Стерлитамак", "Уфимский уезд"),
            binding("Q2", "Башкирская содовая компания", "Стерлитамак", "Башкортостан"),
            binding("Q3", "Черкизово (компания)", "Москва", "Россия"),
            binding("Q4", "Q4"),
        ]
    }
}


def test_parse_wikidata_merges_rows() -> None:
    found = parse_wikidata(PAYLOAD, "Республика Башкортостан")
    assert [item.qid for item in found] == ["Q1", "Q2", "Q3"]
    soda = found[1]
    assert soda.same_region and soda.region == "Стерлитамак"
    assert found[2].name == "Черкизово"
    assert found[0].url == "https://www.wikidata.org/wiki/Q1"


def test_wikidata_query_by_segment() -> None:
    query = wikidata_query("chemicals", 20)
    assert query is not None
    assert "VALUES ?industry { wd:Q207652 }" in query
    assert "LIMIT 20" in query
    assert wikidata_query("other", 20) is None


async def test_search_wikidata_uses_sparql() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=json.dumps(PAYLOAD))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        found = await search_wikidata(client, "chemicals", "Москва", limit=2)
        assert await search_wikidata(client, "other", "Москва") == []
    assert [item.name for item in found] == ["Уралкалий", "Башкирская содовая компания"]
    assert len(seen) == 1
    assert "wd:Q207652" in seen[0].url.params["query"]
    assert seen[0].headers["User-Agent"].startswith("news-insights-assistant")


def test_contract_rules() -> None:
    with pytest.raises(ValidationError):
        Competitor(id="U_2", source=CompetitorSource.BANK_DATA, reasons=["x"])
    with pytest.raises(ValidationError):
        Competitor(id="client:1", source=CompetitorSource.CLIENT, reasons=["x"])
    with pytest.raises(ValidationError):
        Competitor(id="wikidata:Q1", source=CompetitorSource.OPEN_SOURCE, name="X", reasons=[])


def test_demo_legend_has_checked_competitors() -> None:
    legends = load_legends(Path(__file__).resolve().parents[2] / "data/samples/legends.yaml")
    assert set(legends.competitors) == set(legends.companies)
    for entries in legends.competitors.values():
        assert len(entries) >= 3
        assert all(
            (entry.url or "").startswith("https://www.wikidata.org/wiki/Q") for entry in entries
        )
