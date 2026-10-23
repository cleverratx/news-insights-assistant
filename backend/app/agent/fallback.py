"""A5-11: поведение при нехватке данных.

Сервис не додумывает: каждая цифра вывода должна найтись в источниках или данных компании,
а чего не хватило — перечисляется пользователю с последствием и способом восполнить. Если
персональный вывод сформировать нельзя, показываются только проверенные факты из источника.
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from pydantic import JsonValue

from app.agent.context import AnalysisContext, CriticReview
from app.contracts.critic import CriticReport
from app.contracts.generation import DataGap, ExecutionStatus, GapKind
from app.contracts.insight import (
    CheckIssue,
    CheckProblem,
    Insight,
    InsightStatus,
    Origin,
    SourceRef,
    SourceType,
    Statement,
)
from app.contracts.knowledge import KnowledgeBase, Question
from app.contracts.news import FactStatus, MaterialKind, NewsItem, Topic
from app.contracts.profile import CompanyProfile
from app.profile.gaps import gaps

NUMBER_ISSUE = "Цифра без источника"
NUMBER = re.compile(r"(?<![\w.,])[-−]?\d+(?:[   ]\d{3})*(?:[.,]\d+)?(?![\w])")
UNIT = re.compile(
    r"\s*(?:%|‰|п\.\s*п|б\.\s*п|руб|₽|\$|€|долл|евро|юан|млн|млрд|трлн|тыс|раз|т\b|тонн|барр)",
    re.IGNORECASE,
)
SCALES = tuple(Decimal(10) ** power for power in (-9, -6, -3, 0, 3, 6, 9))
SMALL = Decimal(10)
HEADLINE = "headline"
SECTIONS = ("what_happened", "why_it_matters", "risks", "opportunities", "actions", "bank_view")
SHORT_TEXT = 600
MAX_FACTS = 3
MAX_QUESTIONS = 2
TOPIC_QUESTIONS: dict[Topic, tuple[str, ...]] = {
    Topic.FX: ("markets.export_share",),
    Topic.TRADE: ("markets.export_share", "markets.sales_regions"),
    Topic.SANCTIONS: ("markets.export_share", "suppliers.main"),
    Topic.COMMODITIES: ("suppliers.main", "products.main"),
    Topic.LOGISTICS: ("suppliers.main", "markets.sales_regions"),
    Topic.CONSUMPTION: ("customers.main", "seasonality.peak"),
    Topic.KEY_RATE: ("plans.next_year",),
    Topic.DEBT_MARKET: ("plans.next_year",),
    Topic.INFLATION: ("suppliers.main", "plans.next_year"),
    Topic.REGIONAL: ("markets.sales_regions",),
    Topic.LABOR: ("constraints.main",),
    Topic.REGULATION: ("products.main", "constraints.main"),
    Topic.TAXES: ("products.main",),
    Topic.INDUSTRY: ("products.main", "customers.main"),
    Topic.COMPANY: ("customers.main", "suppliers.main"),
}
MODEL_GAPS: dict[ExecutionStatus, tuple[str, str]] = {
    "unavailable": (
        "Языковая модель недоступна: не задан ключ или исчерпан дневной лимит",
        "Повторите запрос позже: лимит обновляется в 03:00 МСК",
    ),
    "planner_error": (
        "Модель не вернула корректный план разбора",
        "Повторите запрос; если ошибка повторится, лимит модели мог закончиться",
    ),
    "timeout": (
        "Разбор не уложился в отведённое время",
        "Повторите запрос или выберите меньше материалов",
    ),
    "step_limit": (
        "Агенту не хватило шагов, чтобы собрать нужные данные",
        "Уточните акцент вопроса, чтобы сузить поиск",
    ),
}


def parse(token: str) -> Decimal | None:
    cleaned = re.sub(r"[   ]", "", token).replace(",", ".").replace("−", "-")
    try:
        return abs(Decimal(cleaned))
    except InvalidOperation:
        return None


def figures(text: str) -> list[tuple[str, Decimal, int]]:
    found = []
    for match in NUMBER.finditer(text):
        value = parse(match.group())
        if value is None:
            continue
        decimals = (
            len(re.split(r"[.,]", match.group())[1]) if re.search(r"[.,]", match.group()) else 0
        )
        has_unit = bool(UNIT.match(text, match.end()))
        if value <= SMALL and decimals == 0 and not has_unit:
            continue
        found.append((match.group().strip(), value, decimals))
    return found


def pool(texts: Iterable[str]) -> set[Decimal]:
    values: set[Decimal] = set()
    for text in texts:
        for match in NUMBER.finditer(text):
            value = parse(match.group())
            if value is not None:
                values.add(value)
    return values


def grounded(value: Decimal, decimals: int, known: set[Decimal]) -> bool:
    step = Decimal(1).scaleb(-decimals)
    for source in known:
        for scale in SCALES:
            candidate = (source * scale).quantize(step, rounding="ROUND_HALF_UP")
            if candidate == value:
                return True
    return False


def ungrounded(text: str, known: set[Decimal]) -> list[str]:
    return [
        token for token, value, decimals in figures(text) if not grounded(value, decimals, known)
    ]


def number_issues(statements: Iterable[Statement], known: set[Decimal]) -> list[CheckIssue]:
    problems = []
    for statement in statements:
        missing = ungrounded(statement.text, known)
        if missing:
            listed = ", ".join(f"«{token}»" for token in dict.fromkeys(missing))
            problems.append(
                CheckIssue(
                    statement_id=statement.id,
                    problem=CheckProblem.UNSUPPORTED,
                    detail=(
                        f"{NUMBER_ISSUE}: {listed} нет в источниках и данных компании. "
                        "Приведи цифру из источника дословно или сформулируй без неё"
                    ),
                    resolved=False,
                )
            )
    return problems


def numbers_only(review: CriticReview) -> list[str] | None:
    issues = review.report.issues
    if not issues or any(
        not i.detail.startswith(NUMBER_ISSUE) or i.statement_id is None for i in issues
    ):
        return None
    return list(dict.fromkeys(i.statement_id for i in issues if i.statement_id))


def disputed(review: CriticReview) -> list[str] | None:
    """Проверка оспорила отдельные утверждения, а не разбор целиком: без них остаток может
    быть верным. Одно спорное утверждение не должно прятать пять проверенных."""
    issues = review.report.issues
    if not issues or any(i.statement_id is None for i in issues):
        return None
    return list(dict.fromkeys(i.statement_id for i in issues if i.statement_id))


def prune(draft: JsonValue, statement_ids: Sequence[str]) -> dict[str, Any] | None:
    if not isinstance(draft, dict):
        return None
    removed = set(statement_ids)
    pruned: dict[str, Any] = dict(draft)
    for section in SECTIONS:
        items = draft.get(section)
        if isinstance(items, list):
            pruned[section] = [
                item for item in items if not (isinstance(item, dict) and item.get("id") in removed)
            ]
    if HEADLINE in removed:
        pruned["headline"] = None
    return pruned


def confirmed_facts(news: Sequence[NewsItem]) -> tuple[list[Statement], list[SourceRef]]:
    statements: list[Statement] = []
    sources: list[SourceRef] = []
    for item in news:
        if item.kind is not MaterialKind.NEWS:
            continue
        for fact in item.key_facts:
            if len(statements) == MAX_FACTS:
                break
            if fact.status not in {FactStatus.CONFIRMED, FactStatus.REPORTED}:
                continue
            source_id = f"fact:{len(statements) + 1}"
            text = fact.text
            if fact.status is FactStatus.REPORTED and fact.source_says:
                text = f"{fact.source_says}: {text}"
            sources.append(
                SourceRef(
                    id=source_id,
                    type=SourceType.NEWS,
                    ref_id=item.id,
                    fragment_id=fact.fragment_id or fact.id,
                    title=item.title,
                    url=item.url,
                )
            )
            statements.append(
                Statement(
                    id=f"fact-statement:{len(statements) + 1}",
                    text=text,
                    origin=Origin.FACT,
                    source_ids=[source_id],
                    confidence="high" if fact.status is FactStatus.CONFIRMED else "medium",
                )
            )
    return statements, sources


def related_questions(profile: CompanyProfile, news: Sequence[NewsItem]) -> list[Question]:
    wanted: list[str] = []
    for item in news:
        topics = item.classification.topics if item.classification else []
        for topic in topics:
            wanted.extend(TOPIC_QUESTIONS.get(topic, ()))
    if not wanted:
        return []
    answered = {feature.id for feature in profile.features}
    open_questions = {
        question.key: question
        for question in gaps(profile, KnowledgeBase(company_id=profile.company_id)).questions
        if f"feature:knowledge:{question.key}" not in answered
    }
    chosen = [open_questions[key] for key in dict.fromkeys(wanted) if key in open_questions]
    return chosen[:MAX_QUESTIONS]


def quantitative(insight: Insight) -> bool:
    return any(figures(item.text) for item in [*insight.risks, *insight.opportunities])


def data_gaps(
    *,
    profile: CompanyProfile,
    news: Sequence[NewsItem],
    insight: Insight,
    execution: ExecutionStatus,
    reviews: Sequence[CriticReview],
    pruned: Sequence[str] = (),
    disputed: Sequence[str] = (),
    reasons: Sequence[str] = (),
) -> list[DataGap]:
    result: list[DataGap] = []
    ready = insight.status is InsightStatus.READY
    shown_facts = (
        "показаны только подтверждённые факты из источника"
        if insight.what_happened
        else ("показаны только ссылки на источники")
    )
    if not ready and execution in MODEL_GAPS:
        missing, remedy = MODEL_GAPS[execution]
        result.append(
            DataGap(
                kind=GapKind.MODEL,
                missing=missing,
                consequence=f"Персональный вывод не сформирован; {shown_facts}",
                remedy=remedy,
                blocking=True,
            )
        )
    elif not ready:
        details = [
            issue.detail
            for issue in (reviews[-1].report.issues if reviews else [])
            if not issue.resolved
        ]
        result.append(
            DataGap(
                kind=GapKind.VERIFICATION,
                missing="Выводы не удалось подтвердить источниками и данными компании",
                consequence=(
                    f"Персональный вывод скрыт, чтобы не ввести в заблуждение; {shown_facts}"
                ),
                remedy="Выберите другой материал по теме или уточните вопрос",
                blocking=True,
                details=list(dict.fromkeys(details))[:5]
                or [insight.status_reason or "Вывод не прошёл проверку"],
            )
        )
    if pruned:
        result.append(
            DataGap(
                kind=GapKind.NUMBERS,
                missing="Для части выводов в источниках нет цифр, которые назвала модель",
                consequence=f"Скрыто выводов: {len(pruned)}; остальное прошло проверку",
                blocking=False,
            )
        )
    if disputed:
        hidden = [statement for statement in disputed if statement != HEADLINE]
        renamed = "; заголовок заменён заголовком материала" if HEADLINE in disputed else ""
        result.append(
            DataGap(
                kind=GapKind.VERIFICATION,
                missing="Часть выводов не подтвердила смысловая проверка",
                consequence=f"Скрыто выводов: {len(hidden)}{renamed}; остальное прошло проверку",
                blocking=False,
                details=list(reasons)[:5],
            )
        )
    if ready and not quantitative(insight):
        result.append(
            DataGap(
                kind=GapKind.NUMBERS,
                missing="В материалах и данных о компании нет цифр для количественной оценки",
                consequence=(
                    "Риски и возможности описаны качественно, без оценки в рублях и процентах"
                ),
                remedy="Ответы на вопросы о компании ниже позволят оценить влияние точнее",
                blocking=False,
            )
        )
    if ready and not insight.bank_view:
        result.append(
            DataGap(
                kind=GapKind.BANK_VIEW,
                missing="Аналитики Альфа-Банка по этой теме не нашлось",
                consequence=(
                    "Раздел «Позиция банка» пуст: выводы ИИ не являются официальной позицией банка"
                ),
                blocking=False,
            )
        )
    for item in news:
        if item.kind is MaterialKind.NEWS and len(item.text) < SHORT_TEXT:
            result.append(
                DataGap(
                    kind=GapKind.SOURCE_TEXT,
                    missing=(
                        f"От материала «{item.title}» доступен только анонс "
                        f"({len(item.text)} знаков)"
                    ),
                    consequence="Выводы опираются на неполный текст",
                    remedy="Полный текст — по ссылке на первоисточник",
                    blocking=False,
                    news_ids=[item.id],
                )
            )
    for question in related_questions(profile, news):
        result.append(
            DataGap(
                kind=GapKind.COMPANY_DATA,
                missing=question.question,
                consequence=question.why,
                remedy="Ответьте в разделе «О компании»: следующий разбор учтёт ответ",
                blocking=False,
                question_keys=[question.key],
            )
        )
    return result


class Reviewer(Protocol):
    async def review(self, context: AnalysisContext, output: JsonValue) -> CriticReport: ...


@dataclass(frozen=True)
class Salvaged:
    draft: dict[str, Any]
    removed: list[str]
    by_numbers: bool
    reasons: list[str] = field(default_factory=list)


async def salvage(context: AnalysisContext, reviewer: Reviewer) -> Salvaged | None:
    """Последний черновик отклонён только из-за отдельных утверждений — цифр без источника
    или замечаний смысловой проверки к ним: убираем эти утверждения и проверяем остаток
    заново, включая смысловую проверку. Общие замечания к разбору не спасаются."""
    if not context.critic_reviews:
        return None
    last = context.critic_reviews[-1]
    by_numbers = True
    removed = numbers_only(last)
    if removed is None:
        by_numbers, removed = False, disputed(last)
    pruned = prune(last.draft, removed) if removed else None
    if removed is None or pruned is None:
        return None
    report = await reviewer.review(context.model_copy(deep=True), pruned)
    context.critic_reviews.append(CriticReview(draft=pruned, report=report))
    if not report.passed:
        return None
    reasons = list(dict.fromkeys(i.detail for i in last.report.issues))
    return Salvaged(pruned, removed, by_numbers, reasons)
