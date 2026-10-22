"""Проверки связей между ролевой лентой и оценкой влияния, а не только отдельных функций."""

import pytest

from app.api import feed as api
from app.contracts.news import Topic
from app.rank.roles import Role, weights_for
from tests.test_api_feed import material
from tests.test_news_classify import NOW
from tests.test_rank_rules import profile


async def test_role_feed_assesses_exactly_displayed_materials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assess = api.assess
    calls = []

    async def recording(llm, company, cards, cache):
        calls.append([n.id for n, _ in cards])
        return await assess(llm, company, cards, cache)

    monkeypatch.setattr(api, "assess", recording)
    news = material("rate", "Изменение ставки", topics=[Topic.KEY_RATE], segments=["oil_gas"])
    irrelevant = material("unrelated", "Другая отрасль", segments=["retail"])
    for role in (Role.CFO, Role.LOGISTICS):
        result = await api.build_feed(
            profile(),
            [news, irrelevant],
            None,
            now=NOW,
            role=weights_for(role),
            min_importance=0,
        )
        assert calls[-1] == [entry.news_id for entry in result.items] == [news.id]
        [shown] = result.items
        assert shown.score.role is not None
        assert result.role == role.value
        assert len(shown.impact.criteria) == 5
        assert -100 <= shown.impact.score <= 100
    assert len(calls) == 2
