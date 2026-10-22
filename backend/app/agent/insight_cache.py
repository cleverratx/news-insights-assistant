"""A5-09: кеш проверенных инсайтов. Тот же профиль, те же материалы, роль и акцент — тот же
разбор без повторного вызова модели, пока он не устарел."""

import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from functools import cache
from typing import Any

from app.agent.critic import Draft
from app.agent.insight import INSTRUCTION
from app.agent.trace import load
from app.cache.store import LLMCache, schema_text, version_of
from app.contracts.generation import GeneratedInsight
from app.contracts.insight import InsightStatus
from app.contracts.news import NewsItem
from app.contracts.profile import CompanyProfile
from app.contracts.trace import AgentTrace
from app.rank.roles import Role
from app.rank.snapshots import digest

KIND = "insight"


@cache
def cache_version() -> str:
    return version_of(INSTRUCTION, schema_text(Draft), schema_text(GeneratedInsight))


def cache_input(
    profile: CompanyProfile, news: Sequence[NewsItem], role: Role | None, focus: str | None
) -> str:
    return json.dumps(
        {
            "company_id": profile.company_id,
            "profile": digest(profile),
            "news": [[item.id, digest(item)] for item in news],
            "role": role.value if role else None,
            "focus": " ".join(focus.split()).casefold() if focus else None,
        },
        ensure_ascii=False,
    )


async def cached(
    connection: Any, text: str, max_age: timedelta, now: datetime | None = None
) -> tuple[GeneratedInsight, AgentTrace] | None:
    moment = now or datetime.now(UTC)
    hit = await LLMCache(connection).get(KIND, cache_version(), text, moment)
    if hit is None or moment - hit.created_at > max_age:
        return None
    insight_id = hit.result.get("insight_id")
    if not isinstance(insight_id, str):
        return None
    saved = await load(connection, insight_id)
    if saved is None or saved[0].insight.status is not InsightStatus.READY:
        return None
    result, trace = saved
    return result.model_copy(update={"cache_hit": True}), trace


async def remember(connection: Any, text: str, result: GeneratedInsight) -> bool:
    if result.insight.status is not InsightStatus.READY or result.execution_status != "completed":
        return False
    await LLMCache(connection).put(
        KIND,
        cache_version(),
        text,
        {"insight_id": result.insight.id},
        ",".join(model.value for model in result.usage.models) or "none",
        result.usage.known_cost_rub,
        result.insight.created_at,
    )
    return True
