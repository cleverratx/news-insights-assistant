"""A5-06/A5-07: генерация проверенного инсайта и явное разделение происхождения выводов."""

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from time import monotonic
from typing import Any
from uuid import uuid4

from pydantic import JsonValue, ValidationError

from app.agent.context import AnalysisContext
from app.agent.critic import Draft, InsightCritic
from app.agent.fallback import confirmed_facts, data_gaps, salvage
from app.agent.offers import offer_for, prompt_hint
from app.agent.orchestrator import SYSTEM_PROMPT, Completer, Limits, LLMPlanner, Orchestrator
from app.agent.tools.builtin import create_tools
from app.agent.trace import build_trace
from app.contracts.common import Significance
from app.contracts.critic import CriticReport
from app.contracts.feed import FeedItem
from app.contracts.generation import (
    BlockId,
    ExecutionStatus,
    GeneratedInsight,
    GenerationUsage,
    InsightBlock,
    InsightPresentation,
    InsightRanking,
    LabeledStatement,
)
from app.contracts.insight import (
    CheckIssue,
    CheckProblem,
    Insight,
    InsightStatus,
    Origin,
    SelfCheck,
    SourceRef,
    SourceType,
    Statement,
)
from app.contracts.news import MaterialKind, NewsItem
from app.contracts.profile import CompanyProfile
from app.contracts.rank import ImportanceLevel
from app.contracts.trace import AgentTrace
from app.llm.models import LLMResult, Message, Task
from app.rank.importance import importance
from app.rank.roles import Role, apply_role, weights_for
from app.rank.score import score
from app.rank.sentiment import rules_impact

INSTRUCTION = (
    SYSTEM_PROMPT
    + """
Готовь персональный инсайт по шести вопросам: что произошло; почему важно этой компании;
какие риски/возможности; что предпринять; как оценивает Альфа-Банк; на чём основано.
Итог — объект по output_schema, без выдуманных цифр, причин и ссылок.
Сначала найди позицию банка через search_bank_analytics, при необходимости уточни оригинал
через get_original. Нет подходящей аналитики после поиска — bank_view=[], не придумывай её.
Не повторяй get_profile: профиль уже передан. Используй реальные id признаков компании.
what_happened — подтверждённые события (origin=fact) либо явно помеченный пересказ модели.
bank_view — только origin=bank, только источники bank_analytics.
why_it_matters, риски, возможности и действия — твоя интерпретация, origin=model,
связь с признаками компании и конкретными источниками. Данные клиента — origin=client_data.
Не выдавай мнение СМИ за позицию банка. Наличие документа банка не подтверждает любое мнение.
Прогноз — не свершившийся факт. Не выдумывай оригинальные цитаты: копируй дословно.
Пиши коротко: одна конкретная мысль на утверждение, короткие цитаты, без повторов и воды.
Не добавляй банковский продукт, если его уместность не следует из данных.
На раздел достаточно 1–2 утверждений; цитаты короткие (до 180 символов).
В каждом why_it_matters обязателен source_id локального источника type=profile_feature;
его ref_id копируй из profile.features[].id. company_id копируй из profile.company_id.
После подходящего поиска и уточнения источника формируй finish. Не перебирай все инструменты.
Не делай больше двух поисков одной темы: отсутствие подходящих данных тоже результат.
Цифры — только из источников и признаков компании, дословно, без пересчёта и прогнозов от себя.
Нет нужной цифры — сформулируй качественно: сервис сам сообщит пользователю, чего не хватило.
"""
)
OBJECTIVE = "Разобрать влияние события на компанию по шести вопросам с проверкой источников"


class GenerationCritic(InsightCritic):
    async def review(self, context: AnalysisContext, output: JsonValue) -> CriticReport:
        try:
            parsed = Draft.model_validate(output)
        except ValidationError:
            return await super().review(context, output)
        outside_bank = [s for s in parsed.statements() if s not in parsed.bank_view]
        if set(parsed.news_ids) != {n.id for n in context.news} or any(
            s.origin is Origin.BANK for s in outside_bank
        ):
            return CriticReport(
                passed=False,
                semantic_checked=False,
                issues=[
                    CheckIssue(
                        problem=CheckProblem.UNSUPPORTED,
                        detail=(
                            "Разбери все выбранные материалы; "
                            "позицию банка помести только в bank_view"
                        ),
                        resolved=False,
                    )
                ],
            )
        return await super().review(context, output)


class RecordingClient:
    def __init__(self, client: Completer) -> None:
        self.client = client
        self.usage = GenerationUsage()

    async def complete(self, task: Task, messages: Sequence[Message]) -> LLMResult:
        result = await self.client.complete(task, messages)
        models = list(dict.fromkeys([*self.usage.models, result.model]))
        self.usage = GenerationUsage(
            calls=self.usage.calls + 1,
            prompt_tokens=self.usage.prompt_tokens + result.usage.prompt_tokens,
            completion_tokens=self.usage.completion_tokens + result.usage.completion_tokens,
            known_cost_rub=self.usage.known_cost_rub + result.cost_rub,
            models=models,
            pricing_modes=list(dict.fromkeys([*self.usage.pricing_modes, result.pricing.mode])),
        )
        return result


class InsightPlanner(LLMPlanner):
    def context_data(self, context: AnalysisContext) -> dict[str, Any]:
        payload = context.planner_data()
        # Полные материалы остаются в серверном контексте для критика и get_original.
        news = []
        for material in context.news:
            news.append(
                {
                    "id": material.id,
                    "title": material.title,
                    "kind": material.kind.value,
                    "source": material.source.model_dump(mode="json"),
                    "url": str(material.url) if material.url else None,
                    "published_at": material.published_at.isoformat(),
                    "text_excerpt": material.text[:2400],
                    "truncated": len(material.text) > 2400,
                    "fragments": [
                        f.model_copy(update={"text": f.text[:600]}).model_dump(mode="json")
                        for f in material.fragments[:3]
                    ],
                    "available_fragment_ids": [f.id for f in material.fragments],
                    "note": "Сокращённая версия; полный источник доступен через get_original",
                }
            )
        payload["news"] = news
        return payload


def present(insight: Insight) -> InsightPresentation:
    groups: dict[Origin, list[LabeledStatement]] = {origin: [] for origin in Origin}
    labels = {
        Origin.FACT: "Факт по источнику",
        Origin.BANK: "Позиция Альфа-Банка",
        Origin.MODEL: "Вывод ИИ",
        Origin.CLIENT_DATA: "Данные о компании",
    }
    for statement in insight.statements():
        groups[statement.origin].append(
            LabeledStatement(
                statement=statement,
                label=labels[statement.origin],
                source_ids=statement.source_ids,
            )
        )
    bank_empty = "Подходящая позиция банка не найдена; вывод ИИ не подменяет её"
    blocks = []
    sections: list[tuple[BlockId, str, Sequence[Statement]]] = [
        ("event", "Что произошло", insight.what_happened),
        ("relevance", "Почему важно компании", insight.why_it_matters),
        ("consequences", "Риски и возможности", [*insight.risks, *insight.opportunities]),
        ("actions", "Что предпринять", insight.actions),
        ("bank", "Позиция Альфа-Банка", insight.bank_view),
    ]
    for key, title, statements in sections:
        blocks.append(
            InsightBlock(
                id=key,
                title=title,
                statement_ids=[s.id for s in statements],
                empty_reason=(
                    bank_empty
                    if key == "bank" and insight.status is InsightStatus.READY
                    else insight.status_reason or "Недостаточно данных"
                )
                if not statements
                else None,
            )
        )
    blocks.append(
        InsightBlock(
            id="basis", title="На чём основано", source_ids=[s.id for s in insight.sources]
        )
    )
    return InsightPresentation(
        facts=groups[Origin.FACT],
        bank_position=groups[Origin.BANK],
        ai_conclusions=groups[Origin.MODEL],
        client_context=groups[Origin.CLIENT_DATA],
        blocks=blocks,
        originals=insight.sources,
    )


def event_sources(news: Sequence[NewsItem]) -> list[SourceRef]:
    return [
        SourceRef(
            id=f"event:{index}",
            type=SourceType.BANK_ANALYTICS
            if n.kind is MaterialKind.BANK_ANALYTICS
            else SourceType.NEWS,
            ref_id=n.id,
            title=n.title,
            url=n.url,
        )
        for index, n in enumerate(news, start=1)
    ]


async def generate_with_trace(
    profile: CompanyProfile,
    news: Sequence[NewsItem],
    connection: Any,
    llm: Completer | None,
    *,
    focus: str | None = None,
    limits: Limits | None = None,
    role: Role | None = None,
    ranking: Sequence[FeedItem] | None = None,
    feed_snapshot_id: str | None = None,
) -> tuple[GeneratedInsight, AgentTrace]:
    started = monotonic()
    if not news or len(news) > 3 or len({n.id for n in news}) != len(news):
        raise ValueError("Нужны 1–3 различных материала")
    now = datetime.now(UTC)
    scored = [score(profile, n, now) for n in news]
    if role is not None:
        scored = [apply_role(s, n, weights_for(role)) for s, n in zip(scored, news, strict=True)]
    levels = {
        ImportanceLevel.CRITICAL: Significance.HIGH,
        ImportanceLevel.IMPORTANT: Significance.HIGH,
        ImportanceLevel.INFO: Significance.MEDIUM,
        ImportanceLevel.BACKGROUND: Significance.LOW,
    }
    rankings = []
    for n, s in zip(news, scored, strict=True):
        card = next((c for c in ranking or [] if c.news_id == n.id), None)
        weight = card.importance if card else importance(s, n.kind)
        rankings.append(
            InsightRanking(
                news_id=n.id,
                score=card.score if card else s.breakdown(),
                importance=weight,
                significance=card.significance if card else levels[weight.level],
                impact=card.impact if card else rules_impact(n, profile),
            )
        )
    best = max(rankings, key=lambda r: r.importance.score)
    usage = GenerationUsage()
    objective = OBJECTIVE + (f". Акцент пользователя: {focus}" if focus else "")
    if role is not None:
        objective += f". Должность: {weights_for(role).title} ({role.value})"
    objective += ". " + prompt_hint(profile, news)
    objective += ". Контекст ранжирования (оценки, а не подтверждённые факты): " + json.dumps(
        [r.model_dump(mode="json") for r in rankings], ensure_ascii=False
    )
    context = AnalysisContext(profile=profile, news=list(news), objective=objective, started_at=now)
    run_id = context.run_id
    execution: ExecutionStatus = "unavailable"
    reason: str | None = "LLM недоступна; проверенный персональный вывод не сформирован"
    check = SelfCheck(
        passed=False,
        iterations=1,
        issues=[
            CheckIssue(
                problem=CheckProblem.OTHER,
                detail=reason or "Вывод не проверен",
                resolved=False,
            )
        ],
    )
    facts, fact_sources = confirmed_facts(news)
    content: dict[str, Any] = {
        "sources": [*event_sources(news), *fact_sources],
        "what_happened": facts,
    }
    pruned: list[str] = []
    headline = news[0].title
    status = InsightStatus.INSUFFICIENT_DATA
    if llm is not None:
        recording = RecordingClient(llm)
        planner = InsightPlanner(recording, system_prompt=INSTRUCTION)
        run = await Orchestrator(
            planner,
            create_tools(connection),
            limits or Limits(timeout_seconds=180),
            critic=GenerationCritic(recording),
        ).run(profile, news, objective)
        context = run.context
        run_id, execution, output = run.context.run_id, run.status, run.output
        if run.status == "check_failed":
            rescued = await salvage(run.context, GenerationCritic(recording))
            if rescued is not None:
                (output, pruned), execution = rescued, "completed"
        usage = recording.usage
        if execution == "completed" and output is not None:
            try:
                parsed = Draft.model_validate(output)
                content = parsed.model_dump(exclude={"headline", "company_id", "news_ids"})
                headline = parsed.headline or headline
                status = InsightStatus.READY
                check = SelfCheck(
                    passed=True, iterations=len(run.context.critic_reviews), issues=[]
                )
                reason = None
            except ValidationError:
                reason = "Проверенный ответ не удалось привести к контракту инсайта"
        else:
            reason = run.error or "Не удалось получить проверенный ответ"
        if status is not InsightStatus.READY:
            issues = (
                run.context.critic_reviews[-1].report.issues if run.context.critic_reviews else []
            )
            check = SelfCheck(
                passed=False,
                iterations=max(1, len(run.context.critic_reviews)),
                issues=issues
                or [
                    CheckIssue(
                        problem=CheckProblem.OTHER,
                        detail=reason or "Вывод не проверен",
                        resolved=False,
                    )
                ],
            )
    metadata: dict[str, Any] = {
        "id": f"insight:{uuid4().hex}",
        "company_id": profile.company_id,
        "news_ids": [n.id for n in news],
        "run_id": run_id,
        "created_at": now,
        "relevance": best.score.final,
        "models": usage.models,
    }
    try:
        insight = Insight(
            **metadata,
            status=status,
            status_reason=reason,
            headline=headline,
            significance=best.significance if status is InsightStatus.READY else Significance.LOW,
            self_check=check,
            **content,
        )
    except ValidationError:
        execution = "check_failed"
        status = InsightStatus.INSUFFICIENT_DATA
        reason = "Ответ не прошёл финальную проверку контракта; неподтверждённые блоки скрыты"
        insight = Insight(
            **metadata,
            status=status,
            status_reason=reason,
            headline=news[0].title,
            significance=Significance.LOW,
            sources=event_sources(news),
            self_check=SelfCheck(
                passed=False,
                iterations=check.iterations,
                issues=[
                    CheckIssue(
                        problem=CheckProblem.OTHER,
                        detail=reason or "Вывод не проверен",
                        resolved=False,
                    )
                ],
            ),
        )
    warnings = []
    if status is not InsightStatus.READY:
        warnings.append(reason or "Вывод не проверен")
    if status is InsightStatus.READY and not insight.bank_view:
        warnings.append("Позиция банка не найдена; это не означает согласия банка с выводами ИИ")
    result = GeneratedInsight(
        insight=insight,
        presentation=present(insight),
        usage=usage,
        execution_status=execution,
        warnings=warnings,
        gaps=data_gaps(
            profile=profile,
            news=news,
            insight=insight,
            execution=execution,
            reviews=context.critic_reviews,
            pruned=pruned,
        ),
        offer=offer_for(insight, profile, news),
        ranking=rankings,
        role=role,
        feed_snapshot_id=feed_snapshot_id,
    )

    return result, build_trace(result, context, monotonic() - started)


async def generate(
    profile: CompanyProfile,
    news: Sequence[NewsItem],
    connection: Any,
    llm: Completer | None,
    *,
    focus: str | None = None,
    limits: Limits | None = None,
    role: Role | None = None,
    ranking: Sequence[FeedItem] | None = None,
    feed_snapshot_id: str | None = None,
) -> GeneratedInsight:
    result, _ = await generate_with_trace(
        profile,
        news,
        connection,
        llm,
        focus=focus,
        limits=limits,
        role=role,
        ranking=ranking,
        feed_snapshot_id=feed_snapshot_id,
    )
    return result
