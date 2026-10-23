"""A5-08: снимок происхождения вывода и атомарное хранение вместе с инсайтом."""

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import HttpUrl

from app.agent.context import AnalysisContext
from app.agent.critic import HEADLINE, Evidence, evidence
from app.contracts.generation import GeneratedInsight
from app.contracts.insight import (
    Basis,
    Insight,
    Origin,
    SourceRef,
    SourceType,
    Statement,
    Support,
)
from app.contracts.trace import (
    AgentTrace,
    Section,
    TraceChunk,
    TraceClaim,
    TraceDocument,
    TraceRejection,
    TraceStep,
)

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
SECTIONS: tuple[Section, ...] = (
    "what_happened",
    "why_it_matters",
    "risks",
    "opportunities",
    "actions",
    "bank_view",
)
QUOTE_LIMIT = 300


def flat(text: str) -> str:
    return " ".join(text.split())


def locate(
    chunks: list[tuple[str | None, str]], fragment_id: str | None, quote: str | None
) -> tuple[str | None, str] | None:
    wanted = flat(quote) if quote else None
    matching = [
        (fragment, text)
        for fragment, text in chunks
        if (fragment_id is None or fragment == fragment_id)
        and (wanted is None or wanted in flat(text))
    ]
    if fragment_id is None and wanted is None:
        return None
    return next((c for c in matching if c[0] is not None), None) or next(iter(matching), None)


def basis_of(
    statement: Statement,
    sources: dict[str, SourceRef],
    actual: dict[str, Evidence],
    labels: dict[str, str],
) -> list[Basis]:
    found = []
    for source_id in statement.source_ids:
        source = sources.get(source_id)
        if source is None:
            continue
        if source.type is SourceType.PROFILE_FEATURE:
            if source.ref_id in labels:
                found.append(
                    Basis(
                        source_id=source_id,
                        type=source.type,
                        ref_id=source.ref_id,
                        quote=labels[source.ref_id],
                    )
                )
            continue
        known = actual.get(source.ref_id)
        if known is None or known.type is not source.type:
            continue
        chunk = locate(known.chunks, source.fragment_id, source.quote)
        if chunk is None:
            continue
        fragment_id, text = chunk
        quote = flat(source.quote) if source.quote else flat(text)[:QUOTE_LIMIT].strip()
        found.append(
            Basis(
                source_id=source_id,
                type=source.type,
                ref_id=source.ref_id,
                fragment_id=fragment_id,
                quote=quote or None,
            )
        )
    return found


def support_of(origin: Origin, basis: list[Basis]) -> Support:
    kinds = {item.type for item in basis}
    documents = kinds - {SourceType.PROFILE_FEATURE}
    if origin is Origin.BANK:
        return Support.SOURCE if SourceType.BANK_ANALYTICS in kinds else Support.ASSUMPTION
    if origin is Origin.FACT:
        return Support.SOURCE if documents else Support.ASSUMPTION
    if origin is Origin.CLIENT_DATA:
        return Support.COMPANY_DATA if SourceType.PROFILE_FEATURE in kinds else Support.ASSUMPTION
    if documents:
        return Support.SOURCE
    return Support.COMPANY_DATA if kinds else Support.ASSUMPTION


def ground(insight: Insight, context: AnalysisContext) -> Insight:
    """Каждое утверждение получает фрагменты, из которых оно получено. Утверждение без
    подтверждающего фрагмента или признака помечается как предположение модели."""
    actual = evidence(context)
    sources = {source.id: source for source in insight.sources}
    labels = {feature.id: feature.label for feature in context.profile.features}
    update: dict[str, Any] = {}
    for section in SECTIONS:
        items = []
        for statement in getattr(insight, section):
            basis = basis_of(statement, sources, actual, labels)
            items.append(
                statement.model_copy(
                    update={"basis": basis, "support": support_of(statement.origin, basis)}
                )
            )
        update[section] = items
    return insight.model_copy(update=update)


def claims_of(insight: Insight) -> list[TraceClaim]:
    return [
        TraceClaim(
            statement_id=statement.id,
            section=section,
            origin=statement.origin,
            support=statement.support or Support.ASSUMPTION,
            basis=statement.basis,
        )
        for section in SECTIONS
        for statement in getattr(insight, section)
    ]


def rejected_of(context: AnalysisContext, insight: Insight) -> list[TraceRejection]:
    final = {(s.id, s.text) for s in insight.statements()} | {(HEADLINE, insight.headline)}
    found = []
    for index, review in enumerate(context.critic_reviews):
        draft = review.draft if isinstance(review.draft, dict) else {}
        problems: dict[str, list[str]] = {}
        for issue in review.report.issues:
            if issue.statement_id is not None:
                problems.setdefault(issue.statement_id, []).append(issue.detail)
        for statement_id, details in problems.items():
            located = locate_statement(draft, statement_id)
            if located is None:
                continue
            section, origin, text = located
            found.append(
                TraceRejection(
                    check_index=index,
                    statement_id=statement_id,
                    section=section,
                    origin=origin,
                    text=text,
                    problems=list(dict.fromkeys(details)),
                    shown=(statement_id, text) in final,
                )
            )
    return found


def locate_statement(
    draft: dict[str, Any], statement_id: str
) -> tuple[Section | Literal["headline"], Origin | None, str] | None:
    headline = draft.get(HEADLINE)
    if statement_id == HEADLINE:
        return ("headline", None, headline) if isinstance(headline, str) and headline else None
    for section in SECTIONS:
        items = draft.get(section)
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict) or item.get("id") != statement_id:
                continue
            text = item.get("text")
            if not isinstance(text, str) or not text.strip():
                return None
            origin = item.get("origin")
            known = origin if isinstance(origin, str) and origin in set(Origin) else None
            return section, Origin(known) if known else None, text
    return None


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
        status = step.status if step else "ok"
        if step is None and check_index is not None:
            status = "ok" if context.critic_reviews[check_index].report.passed else "error"
        elif step is None and result.execution_status == "timeout":
            status = "timeout"
        steps.append(
            TraceStep(
                number=plan.number,
                action=plan.decision.action,
                goal=plan.decision.reason,
                tool=plan.decision.tool,
                arguments=plan.decision.arguments,
                status=status,
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
        ranking=result.ranking,
        role=result.role,
        feed_snapshot_id=result.feed_snapshot_id,
        claims=claims_of(result.insight),
        rejected=rejected_of(context, result.insight),
    ).model_copy(deep=True)


async def ensure_storage(connection: Any) -> None:
    await connection.execute(SCHEMA)


async def save(connection: Any, result: GeneratedInsight, trace: AgentTrace) -> None:
    if (
        (trace.insight_id, trace.run_id, trace.company_id)
        != (result.insight.id, result.insight.run_id, result.insight.company_id)
        or trace.news_ids != result.insight.news_ids
        or trace.used_sources != result.insight.sources
        or trace.ranking != result.ranking
        or trace.role != result.role
        or trace.feed_snapshot_id != result.feed_snapshot_id
        or trace.usage != result.usage
        or trace.execution_status != result.execution_status
        or trace.claims != claims_of(result.insight)
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
        or trace.ranking != result.ranking
        or trace.role != result.role
        or trace.feed_snapshot_id != result.feed_snapshot_id
        or trace.usage != result.usage
        or trace.execution_status != result.execution_status
    ):
        raise ValueError("Несогласованный сохранённый разбор")
    return result, trace
