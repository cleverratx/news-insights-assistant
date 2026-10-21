import sys
from datetime import timedelta
from pathlib import Path
from types import ModuleType

import pytest

from app.contracts.news import KeyFact, MaterialKind
from tests.test_api_feed import material
from tests.test_news_classify import NOW
from tests.test_rank_rules import profile

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


@pytest.fixture
def evaluation(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.syspath_prepend(str(SCRIPTS))
    monkeypatch.delitem(sys.modules, "eval_relevance", raising=False)
    import eval_relevance

    return eval_relevance


def test_precision_counts_short_feed_as_misses(evaluation: ModuleType) -> None:
    labels = {("U_1", "a"): 2, ("U_1", "b"): 1, ("U_1", "c"): 0}
    full = evaluation.precision_at("U_1", ["a", "b", "c", "d", "e"], labels)
    assert (full.value, full.strict) == (0.4, 0.2)
    assert full.unjudged == ["d", "e"]
    short = evaluation.precision_at("U_1", ["a"], labels)
    assert short.value == 0.2 and short.unjudged == []
    assert evaluation.ceiling("U_1", labels) == 0.4
    assert evaluation.jaccard(["a", "b"], ["b", "c"]) == pytest.approx(1 / 3)


def test_labels_outside_scale_are_rejected(evaluation: ModuleType, tmp_path: Path) -> None:
    rows = [dict.fromkeys(evaluation.FIELDS, "") | {"company_id": "U_1", "news_id": "a"}]
    path = tmp_path / "labels.csv"
    evaluation.write_labels([rows[0] | {"label": "3"}], path)
    with pytest.raises(ValueError, match="вне шкалы"):
        evaluation.read_labels(path)
    evaluation.write_labels([rows[0] | {"label": ""}], path)
    assert evaluation.read_labels(path) == {}


def test_snapshot_keeps_no_source_text(evaluation: ModuleType) -> None:
    fact = KeyFact(id="f", text="Выручка выросла на 14%", status="confirmed", numbers=["14%"])
    news = material("n", "Новость").model_copy(
        update={"text": "Абзац. " * 200, "key_facts": [fact]}
    )
    kept = evaluation.sanitize(news)
    assert len(kept.text) <= evaluation.LEAD + 1
    assert kept.key_facts[0].text == evaluation.FACT_TEXT
    assert kept.key_facts[0].numbers == ["14%"]
    bank = evaluation.sanitize(news.model_copy(update={"kind": MaterialKind.BANK_ANALYTICS}))
    assert (bank.text, bank.summary) == (news.title, None)


def test_baseline_is_newest_first_one_per_story(evaluation: ModuleType) -> None:
    items = [
        material("old", "Старое", age=timedelta(days=2)),
        material("a", "Сюжет", cluster="s", age=timedelta(hours=1)),
        material("b", "Сюжет повтор", cluster="s"),
        material("gone", "За окном", age=timedelta(days=30)),
    ]
    snapshot = evaluation.Snapshot(now=NOW, profiles=[profile()], materials=items)
    assert evaluation.baseline_ranking(snapshot, 5) == ["b", "old"]


async def test_reference_labels_match_snapshot(evaluation: ModuleType) -> None:
    snapshot = evaluation.load_snapshot()
    labels = evaluation.read_labels()
    known = {item.id for item in snapshot.materials}
    companies = {item.company_id for item in snapshot.profiles}
    assert len(labels) >= 50
    assert all(company in companies and news in known for company, news in labels)
    report = await evaluation.score(snapshot, labels)
    assert "| **Среднее** |" in report
