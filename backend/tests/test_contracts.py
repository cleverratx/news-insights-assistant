import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from app.contracts import CompanyProfile, Insight, NewsItem, RankedItem
from app.contracts.__main__ import rendered
from app.contracts.knowledge import KnowledgeBase, KnowledgeGaps

EXAMPLES = Path(__file__).resolve().parents[2] / "docs/schemas/examples"


def example(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((EXAMPLES / f"{name}.json").read_text(encoding="utf-8"))
    return data


@pytest.mark.parametrize(
    ("name", "model"),
    [
        ("company_profile", CompanyProfile),
        ("news_item", NewsItem),
        ("bank_analytics_item", NewsItem),
        ("ranked_item", RankedItem),
        ("insight", Insight),
        ("knowledge_base", KnowledgeBase),
        ("knowledge_gaps", KnowledgeGaps),
    ],
)
def test_examples_are_valid(name: str, model: type[BaseModel]) -> None:
    parsed = model.model_validate(example(name))
    assert model.model_validate_json(parsed.model_dump_json()) == parsed


def test_saved_schemas_match_code() -> None:
    for path, content in rendered().items():
        assert (
            path.read_text(encoding="utf-8") == content
        ), f"{path.name} устарела: запустите uv run python -m app.contracts"


def broken_insight(**changes: Any) -> dict[str, Any]:
    data = example("insight")
    data.update(changes)
    return data


def test_statement_must_cite_known_source() -> None:
    data = broken_insight()
    data["risks"][0]["source_ids"] = ["s404"]
    with pytest.raises(ValidationError, match="неизвестные источники: s404"):
        Insight.model_validate(data)


def test_statement_needs_at_least_one_source() -> None:
    data = broken_insight()
    data["what_happened"][0]["source_ids"] = []
    with pytest.raises(ValidationError):
        Insight.model_validate(data)


def test_bank_position_requires_bank_material() -> None:
    data = broken_insight()
    data["bank_view"][0]["source_ids"] = ["s1"]
    with pytest.raises(ValidationError, match="не опирается на подходящий источник"):
        Insight.model_validate(data)


def test_fact_cannot_rest_on_profile_only() -> None:
    data = broken_insight()
    data["what_happened"][0]["source_ids"] = ["s3"]
    with pytest.raises(ValidationError, match="не опирается на подходящий источник"):
        Insight.model_validate(data)


def test_bank_view_holds_only_bank_position() -> None:
    data = broken_insight()
    data["bank_view"][0]["origin"] = "model"
    with pytest.raises(ValidationError, match="только позиция банка"):
        Insight.model_validate(data)


def test_why_it_matters_must_use_company_feature() -> None:
    data = broken_insight()
    data["why_it_matters"][0]["source_ids"] = ["s1"]
    with pytest.raises(ValidationError, match="без признака компании"):
        Insight.model_validate(data)


def test_ready_insight_needs_risk_or_opportunity() -> None:
    with pytest.raises(ValidationError, match="риск или возможность"):
        Insight.model_validate(broken_insight(risks=[], opportunities=[]))


def test_every_material_is_among_sources() -> None:
    news_ids = ["news:demo-key-rate", "bank:demo-key-rate-review", "news:other"]
    with pytest.raises(ValidationError, match="news_ids"):
        Insight.model_validate(broken_insight(news_ids=news_ids))


def test_bank_product_only_on_product_action() -> None:
    data = broken_insight()
    data["actions"][0]["product"] = "deposit"
    with pytest.raises(ValidationError, match="kind=bank_product"):
        Insight.model_validate(data)


def test_duplicate_statement_ids_rejected() -> None:
    data = broken_insight()
    data["risks"][0]["id"] = "w1"
    with pytest.raises(ValidationError, match="утверждений: w1"):
        Insight.model_validate(data)


def test_no_significant_link_is_valid_answer_with_reason() -> None:
    empty = {
        "what_happened": [],
        "why_it_matters": [],
        "risks": [],
        "opportunities": [],
        "actions": [],
        "bank_view": [],
        "status": "no_significant_link",
    }
    with pytest.raises(ValidationError, match="status_reason"):
        Insight.model_validate(broken_insight(**empty))
    reason = "Компания не зависит от заёмного финансирования и не ведёт ВЭД"
    insight = Insight.model_validate(broken_insight(**empty, status_reason=reason))
    assert list(insight.statements()) == []


def test_unknown_fields_rejected() -> None:
    with pytest.raises(ValidationError, match="extra"):
        RankedItem.model_validate({**example("ranked_item"), "comment": "лишнее"})


def test_reranked_item_requires_llm_score() -> None:
    data = example("ranked_item")
    data["score"]["llm"] = None
    with pytest.raises(ValidationError, match="оценка модели"):
        RankedItem.model_validate(data)


def test_fact_must_point_to_existing_fragment() -> None:
    data = example("news_item")
    data["key_facts"][0]["fragment_id"] = "p9"
    with pytest.raises(ValidationError, match="несуществующие фрагменты: f1"):
        NewsItem.model_validate(data)


def test_news_dates_must_carry_timezone() -> None:
    data = example("news_item")
    data["published_at"] = "2026-09-11T13:30:00"
    with pytest.raises(ValidationError):
        NewsItem.model_validate(data)


def test_market_rank_within_peer_count() -> None:
    data = example("company_profile")
    data["market_position"]["revenue_rank"] = 341
    with pytest.raises(ValidationError, match="больше числа компаний"):
        CompanyProfile.model_validate(data)


def test_profile_feature_ids_unique() -> None:
    data = example("company_profile")
    data["features"].append(data["features"][0])
    with pytest.raises(ValidationError, match="признаков: feature:industry"):
        CompanyProfile.model_validate(data)
