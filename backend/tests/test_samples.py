import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import asyncpg
import pytest

from app.contracts.news import Topic
from app.data.loader import read_case, store
from tests.test_api_companies import call
from tests.test_data_loader import database

__all__ = ["database"]

ROOT = Path(__file__).resolve().parents[2]
SAMPLES = ROOT / "data" / "samples"
COMPANIES = SAMPLES / "df_Uid_data_hack.csv"
LINKS = SAMPLES / "df_Fid_Uid_link_hack.csv"


@pytest.fixture
def generator(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    scripts = ROOT / "backend" / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location("make_samples", scripts / "make_samples.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "make_samples", module)
    spec.loader.exec_module(module)
    return module


def test_committed_samples_match_generator(generator: ModuleType) -> None:
    for name, content in generator.render().items():
        assert (SAMPLES / name).read_text(
            encoding="utf-8-sig"
        ) == content, f"{name} устарел: uv run python scripts/make_samples.py"


def test_samples_exercise_every_cleaning_rule() -> None:
    report = read_case(COMPANIES, LINKS).report()
    problems = {(item["table"], item["problem"]) for item in report["issues"]}
    assert problems >= {
        ("companies", "лишние поля в строке, строка отброшена"),
        ("companies", "не хватает полей в строке"),
        ("companies", "флаг не распознан"),
        ("companies", "значение вне справочника"),
        ("companies", "страны ФТС при FTS_FLAG = 0"),
        ("companies", "код ОКВЭД не распознан"),
        ("companies", "нулевое значение"),
        ("companies", "отрицательное значение"),
        ("companies", "дубликат, оставлена полная строка"),
        ("payments", "нет суммы, строка отброшена"),
        ("payments", "платёж самому себе, строка отброшена"),
        ("persons", "нет идентификатора, строка отброшена"),
    }
    assert report["tables"]["companies"]["loaded"] == 71
    assert report["tables"]["payments"]["loaded"] == 14
    assert report["warnings"] == []


def by_id(profile: dict[str, Any]) -> dict[str, Any]:
    return {feature["id"]: feature for feature in profile["features"]}


def exposures(profile: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    found: dict[str, list[dict[str, Any]]] = {}
    for item in profile["exposures"]:
        found.setdefault(item["topic"], []).append(item)
    return found


async def test_end_to_end_profiles_on_samples(database: asyncpg.Connection) -> None:
    await store(database, read_case(COMPANIES, LINKS))

    importer = (await call(database, "/api/companies/U_900001/profile")).json()
    features = by_id(importer)
    assert importer["industry"]["segment"] == "food"
    assert importer["group"]["companies_count"] == 4
    assert importer["market_position"]["peer_count"] == 13
    assert features["feature:debt_dependence"]["value"] == "high"
    assert features["feature:trade_direction"]["value"] == "import"
    assert features["feature:customer_concentration"]["value"] == "high"
    assert "feature:person_links" in features
    topics = exposures(importer)
    assert topics["fx"][0]["direction"] == "risk"
    assert topics["key_rate"][0]["direction"] == "risk"
    assert {Topic.SANCTIONS.value, Topic.COMPANY.value, Topic.REGIONAL.value} <= set(topics)
    assert any("Гостиницы, общепит" in item["reason"] for item in topics["industry"])

    medtech = (await call(database, "/api/companies/U_900002/profile")).json()
    features = by_id(medtech)
    assert medtech["industry"]["segment"] == "healthcare"
    assert features["feature:customer_concentration"]["value"] == "high"
    assert "feature:foreign_capital" in features
    assert exposures(medtech)["key_rate"][0]["direction"] == "mixed"

    software = (await call(database, "/api/companies/U_900003/profile")).json()
    assert software["industry"]["segment"] == "it_telecom"
    assert "feature:trade_direction" not in by_id(software)


@pytest.mark.parametrize(
    "company_id", [f"U_9004{number:02d}" for number in range(1, 18)] + ["U_900418"]
)
async def test_noisy_companies_answer_not_fail(
    database: asyncpg.Connection, company_id: str
) -> None:
    await store(database, read_case(COMPANIES, LINKS))
    response = await call(database, f"/api/companies/{company_id}/profile")
    assert response.status_code == 200, response.text


async def test_rejected_row_is_not_found(database: asyncpg.Connection) -> None:
    await store(database, read_case(COMPANIES, LINKS))
    assert (await call(database, "/api/companies/U_900419/profile")).status_code == 404
