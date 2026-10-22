"""A5-08: снимок происхождения вывода и атомарное хранение вместе с инсайтом."""

from datetime import UTC, datetime
from typing import Any

from pydantic import HttpUrl

from app.agent.context import AnalysisContext
from app.agent.critic import evidence
from app.contracts.generation import GeneratedInsight
from app.contracts.insight import SourceType
from app.contracts.trace import AgentTrace, TraceChunk, TraceDocument, TraceStep

SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_insights (
    insight_id text PRIMARY KEY,
    run_id text NOT NULL UNIQUE,
    company_id text NOT NULL,
    created_at timestamptz NOT NULL,
    result jsonb NOT NULL,
    trace jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS agent_insights_company ON agent_insights (company_id, created_at DESC);
"""
INSERT = """
INSERT INTO agent_insights (insight_id, run_id, company_id, created_at, result, trace)
VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb)
"""
SELECT = "SELECT result, trace FROM agent_insights WHERE insight_id = $1"


def build_trace(
    result: GeneratedInsight, context: AnalysisContext, elapsed_seconds: float
) -> AgentTrace:
    documents = [
        TraceDocument(
            ref_id=ref_id,
            type=source.type,
            url=HttpUrl(source.url) if source.url else None,
            chunks=[
                TraceChunk(fragment_id=fragment, text=text) for fragment, text in source.chunks
            ],
        )
        for ref_id, source in evidence(context).items()
        if source.type is not SourceType.PROFILE_FEATURE
    ]
    executed = {step.number: step for step in context.steps}
    steps = []
    review_index = 0
    for plan in context.plans:
        step = executed.get(plan.number)
        check_index = None
        if plan.decision.action == "finish" and review_index < len(context.critic_reviews):
            check_index = review_index
            review_index += 1
        steps.append(
            TraceStep(
                number=plan.number,
                action=plan.decision.action,
                goal=plan.decision.reason,
                tool=plan.decision.tool,
                arguments=plan.decision.arguments,
                status=step.status if step else "ok",
                elapsed_seconds=step.elapsed_seconds if step else 0,
                planning_seconds=plan.elapsed_seconds,
                check_index=check_index,
                observation_id=step.observation_id if step else None,
                reused_from=step.reused_from if step else None,
                error_detail=step.error_detail if step else None,
            )
        )
    return AgentTrace(
        insight_id=result.insight.id,
        run_id=result.insight.run_id,
        company_id=result.insight.company_id,
        news_ids=result.insight.news_ids,
        objective=context.objective,
        started_at=context.started_at,
        finished_at=datetime.now(UTC),
        execution_status=result.execution_status,
        error=result.insight.status_reason,
        elapsed_seconds=elapsed_seconds,
        steps=steps,
        observations=list(context.observations.values()),
        checks=[review.report for review in context.critic_reviews],
        documents=documents,
        profile_features=context.profile.features,
        used_sources=result.insight.sources,
        usage=result.usage,
    ).model_copy(deep=True)


async def ensure_storage(connection: Any) -> None:
    await connection.execute(SCHEMA)


async def save(connection: Any, result: GeneratedInsight, trace: AgentTrace) -> None:
    if (
        (trace.insight_id, trace.run_id, trace.company_id)
        != (result.insight.id, result.insight.run_id, result.insight.company_id)
        or trace.news_ids != result.insight.news_ids
        or trace.used_sources != result.insight.sources
        or trace.usage != result.usage
        or trace.execution_status != result.execution_status
    ):
        raise ValueError("Инсайт и трасса относятся к разным разборам")
    # Одна вставка: невозможно сохранить только инсайт или только трассу.
    await connection.execute(
        INSERT,
        result.insight.id,
        result.insight.run_id,
        result.insight.company_id,
        result.insight.created_at,
        result.model_dump_json(),
        trace.model_dump_json(),
    )


async def load(connection: Any, insight_id: str) -> tuple[GeneratedInsight, AgentTrace] | None:
    row = await connection.fetchrow(SELECT, insight_id)
    if row is None:
        return None
    result = GeneratedInsight.model_validate_json(row["result"])
    trace = AgentTrace.model_validate_json(row["trace"])
    if (
        result.insight.id != insight_id
        or (trace.insight_id, trace.run_id, trace.company_id)
        != (result.insight.id, result.insight.run_id, result.insight.company_id)
        or trace.news_ids != result.insight.news_ids
        or trace.used_sources != result.insight.sources
        or trace.usage != result.usage
        or trace.execution_status != result.execution_status
    ):
        raise ValueError("Несогласованный сохранённый разбор")
    return result, trace
