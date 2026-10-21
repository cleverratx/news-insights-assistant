import sys
from pathlib import Path
from types import ModuleType

import pytest

from app.api.feed import build_feed
from app.contracts.feed import Feed
from tests.test_api_feed import material
from tests.test_news_classify import NOW
from tests.test_rank_rules import profile

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


@pytest.fixture
def holdout(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.syspath_prepend(str(SCRIPTS))
    for name in ("eval_holdout", "eval_relevance"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    import eval_holdout

    return eval_holdout


async def feed_of(items: list) -> Feed:  # type: ignore[type-arg]
    return await build_feed(profile(), items, None, now=NOW)


async def test_inspect_catches_broken_feeds(holdout: ModuleType) -> None:
    oil = [
        material("o1", "Нефть", segments=["oil_gas"]),
        material("o2", "Регион", regions=["Свердловская область"]),
        material("o3", "Ставка", topics=["key_rate"], segments=["oil_gas"]),
    ]
    feed = await feed_of(oil)
    assert holdout.inspect(feed, profile(), oil, False) == []
    empty = feed.model_copy(update={"items": []})
    assert holdout.inspect(empty, profile(), oil, False) == ["лента пустая"]
    stranger = holdout.inspect(feed, profile(features=["size"]), oil, False)
    assert any("признаков нет в профиле" in problem for problem in stranger)
    swapped = feed.model_copy(update={"items": list(reversed(feed.items))})
    assert "места идут не подряд" in holdout.inspect(swapped, profile(), oil, False)
    assert "verified=False, ожидалось True" in holdout.inspect(feed, profile(), oil, True)
    bare = [item.model_copy(update={"classification": None}) for item in oil]
    assert any("без разбора" in p for p in holdout.inspect(feed, profile(), bare, False))


async def test_inspect_catches_runs_of_one_event(holdout: ModuleType) -> None:
    same = [
        material(f"r{index}", f"Роснефть {index}", entities=["Роснефть"], segments=["oil_gas"])
        for index in range(3)
    ]
    region = material("x", "Регион", regions=["Свердловская область"])
    feed = await feed_of(same)
    assert holdout.inspect(feed, profile(), same, False) == []
    stuck = feed.model_copy(
        update={
            "items": [
                *feed.items,
                (await feed_of([region])).items[0].model_copy(update={"rank": 4}),
            ]
        }
    )
    problems = holdout.inspect(stuck, profile(), [*same, region], False)
    assert any("подряд об одном событии" in problem for problem in problems)


async def test_fake_models_cover_fallback_paths(holdout: ModuleType) -> None:
    oil = [material("o", "Нефть", segments=["oil_gas"])]
    confirmed = await build_feed(profile(), oil, holdout.Echo(), now=NOW)
    assert confirmed.verified and confirmed.items[0].explanation == "Подтверждено тестовой моделью"
    for broken in (holdout.Garbage(), holdout.Down()):
        fallback = await build_feed(profile(), oil, broken, now=NOW)
        assert not fallback.verified and len(fallback.items) == 1


def test_noise_keeps_every_material(holdout: ModuleType) -> None:
    items = [material(f"n{index}", f"Новость {index}") for index in range(6)]
    noisy = holdout.noisy(items, NOW)
    assert len(noisy) == 7
    assert noisy[0].classification is None
    assert noisy[3].published_at > NOW
    assert noisy[5].id == "n4:copy"


async def test_holdout_feed_never_breaks(holdout: ModuleType) -> None:
    snapshot = holdout.reference.load_snapshot(holdout.SNAPSHOT)
    outcomes = await holdout.run_checks(snapshot)
    assert len(outcomes) == 21
    assert [outcome for outcome in outcomes if outcome.violations] == []
