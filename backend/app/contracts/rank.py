from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, model_validator

from app.contracts.common import (
    SCHEMA_VERSION,
    Contract,
    Id,
    Score,
    Significance,
    Text,
    require_unique,
)


class RankStage(StrEnum):
    CANDIDATE = "candidate"
    RERANKED = "reranked"


class FeatureMatch(Contract):
    feature_id: Id = Field(description="Идентификатор признака из CompanyProfile.features")
    reason: Text
    rule: Id | None = Field(default=None, description="Правило сопоставления, которое сработало")
    weight: Score | None = Field(default=None, description="Вес признака по этому правилу")
    contribution: Score | None = Field(
        default=None, description="Вклад признака в итоговый балл: сумма вкладов равна final"
    )


class RoleAdjustment(Contract):
    id: Id
    title: Text
    multiplier: float = Field(ge=0.2, le=2, allow_inf_nan=False)
    company_relevance: Score
    role_relevance: Score
    detail: Text


class ScoreBreakdown(Contract):
    role: RoleAdjustment | None = None
    lexical: float | None = Field(default=None, ge=0, allow_inf_nan=False, description="Сырой BM25")
    lexical_norm: Score | None = None
    llm: Score | None = Field(default=None, description="Оценка переранжирования моделью")
    relevance: Score | None = Field(
        default=None, description="Совпадение с признаками компании по правилам отбора"
    )
    recency: Score | None = Field(
        default=None, description="Свежесть: 1 — только что, 0,5 — полураспад"
    )
    scale: Score | None = Field(
        default=None, description="Масштаб события: тема, охват изданий, подтверждённые цифры"
    )
    final: Score
    factors: list[Text] = Field(
        default_factory=list,
        description="Пояснения к баллу для интерфейса: свежесть, масштаб, штрафы",
    )


class RankedItem(Contract):
    """Материал ленты, отобранный под конкретную компанию, с объяснением, почему он выше других."""

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    company_id: Id
    news_id: Id
    stage: RankStage
    rank: int = Field(ge=1)
    score: ScoreBreakdown
    significance: Significance
    matched_features: list[FeatureMatch] = Field(default_factory=list)
    matched_terms: list[Text] = Field(default_factory=list)
    reason: Text | None = Field(default=None, description="Короткое объяснение от модели")
    run_id: Id | None = None

    @model_validator(mode="after")
    def reranked_has_llm_score(self) -> Self:
        require_unique((match.feature_id for match in self.matched_features), "признаков")
        if self.stage is RankStage.RERANKED and self.score.llm is None:
            raise ValueError("После переранжирования должна быть оценка модели")
        return self


class ImportanceLevel(StrEnum):
    CRITICAL = "critical"
    IMPORTANT = "important"
    INFO = "info"
    BACKGROUND = "background"


class ImportanceFactor(Contract):
    """Слагаемое балла важности: сколько баллов дал фактор и почему."""

    id: Id = Field(description="relevance, breadth, scale, recency, model, bank")
    title: Text
    points: float = Field(ge=0, le=10, allow_inf_nan=False)
    max_points: float = Field(gt=0, le=10, allow_inf_nan=False)
    detail: Text


class Importance(Contract):
    """Степень важности материала для компании: 0–10, уровень и вклад каждого фактора.
    Сумма points всех факторов равна score."""

    score: float = Field(ge=0, le=10, allow_inf_nan=False)
    level: ImportanceLevel
    title: Text = Field(description="Критично, Важно, К сведению, Фон")
    factors: list[ImportanceFactor] = Field(min_length=1)

    @model_validator(mode="after")
    def factors_add_up(self) -> Self:
        if abs(sum(factor.points for factor in self.factors) - self.score) > 0.051:
            raise ValueError("Сумма вкладов факторов не равна баллу важности")
        return self


class ImportanceScale(Contract):
    """Критерий уровня для пользователя: с какого балла и что это значит."""

    level: ImportanceLevel
    title: Text
    min_score: float = Field(ge=0, le=10, allow_inf_nan=False)
    description: Text


LEVEL_POINTS = {-2: -50, -1: -20, 0: 0, 1: 20, 2: 50}


class ImpactCriterionId(StrEnum):
    REVENUE = "revenue"
    COSTS = "costs"
    MARKET_ACCESS = "market_access"
    COST_OF_MONEY = "cost_of_money"
    REGULATORY = "regulatory"
    LIQUIDITY = "liquidity"
    FX = "fx"
    COUNTERPARTIES = "counterparties"
    COMPETITION = "competition"


class ImpactTiming(StrEnum):
    NOW = "now"
    QUARTER = "quarter"
    YEAR = "year"
    LATER = "later"


TIMING_TITLES = {
    ImpactTiming.NOW: "сразу, в течение месяца",
    ImpactTiming.QUARTER: "в ближайшие 3 месяца",
    ImpactTiming.YEAR: "через 3–12 месяцев",
    ImpactTiming.LATER: "больше чем через год",
}


class ImpactColor(StrEnum):
    RED = "red"
    ORANGE = "orange"
    GRAY = "gray"
    LIGHT_GREEN = "light_green"
    GREEN = "green"


class ImpactCriterion(Contract):
    """Влияние события на один фиксированный критерий: уровень от −2 до +2 и почему.
    Плюс — лучше для компании: для регуляторного риска плюс значит, что риск снижается."""

    id: ImpactCriterionId
    title: Text
    level: int = Field(ge=-2, le=2, description="−2 существенно хуже … +2 существенно лучше")
    points: int = Field(
        ge=-100,
        le=100,
        description="Вклад в итог: сумма уровней ответов группы (scale=levels) "
        "или ±20/±50 в оценках до перехода на сумму (scale=points)",
    )
    reason: Text | None = Field(default=None, description="Почему такой уровень; пусто при 0")
    timing: ImpactTiming | None = Field(default=None, description="Когда скажется на компании")
    timing_title: Text | None = Field(default=None, description="Срок словами для карточки")
    timing_source: Literal["source", "estimate"] | None = Field(
        default=None,
        description="source — срок назван в материале, estimate — оценка ИИ",
    )

    @model_validator(mode="after")
    def timed(self) -> Self:
        check_timing(self.level, self.timing, self.timing_title, self.timing_source)
        return self


def check_timing(
    level: int,
    timing: ImpactTiming | None,
    title: str | None,
    source: Literal["source", "estimate"] | None,
) -> None:
    if (timing is None) != (source is None):
        raise ValueError("Срок влияния указывается вместе с его основанием")
    if timing is not None and level == 0:
        raise ValueError("Срок есть только у ненулевого влияния")
    if title != (TIMING_TITLES[timing] if timing else None):
        raise ValueError("Подпись срока не соответствует сроку")


class ImpactFeature(Contract):
    """Признак компании, через который канал влияния у неё есть: кредиты, импорт, контрагент."""

    id: Id = Field(description="ProfileFeature.id")
    label: Text


class ImpactWeight(Contract):
    """Как получен вклад ответа: сила до сведения — база уровня, умноженная на срок,
    подтверждение канала данными компании и пересечение с другими ответами критерия; затем
    сила стороны (рисков или возможностей) делится между ответами пропорционально силе."""

    base: int = Field(description="«Заметно» — 30, «существенно» — 60")
    timing: float = Field(gt=0, le=1, allow_inf_nan=False, description="Множитель срока")
    exposure: float = Field(
        gt=0, le=1, allow_inf_nan=False, description="1 — канал подтверждён данными компании"
    )
    overlap: float = Field(
        gt=0,
        le=1,
        allow_inf_nan=False,
        description="0,5 — не первый ответ того же знака в критерии",
    )
    strength: float = Field(ge=0, le=100, allow_inf_nan=False, description="Сила до сведения")
    side_strength: float = Field(
        ge=0, allow_inf_nan=False, description="Сумма сил всех ответов той же стороны"
    )
    side: int = Field(ge=0, le=100, description="Сила стороны после насыщения")

    @model_validator(mode="after")
    def multiplied(self) -> Self:
        if abs(self.base * self.timing * self.exposure * self.overlap - self.strength) > 0.06:
            raise ValueError("Сила ответа не равна произведению множителей")
        if self.side_strength + 0.06 < self.strength:
            raise ValueError("Сила стороны меньше силы её ответа")
        return self


class ImpactAnswer(Contract):
    """Ответ на один из общих вопросов о том, через что событие доходит до бизнеса компании."""

    question_id: Id = Field(description="Идентификатор вопроса, например costs.suppliers")
    question: Text = Field(description="Вопрос словами для сводки по клику")
    criterion: ImpactCriterionId
    level: int = Field(ge=-2, le=2, description="−2 существенно хуже … +2 существенно лучше")
    reason: Text
    timing: ImpactTiming | None = None
    timing_title: Text | None = None
    timing_source: Literal["source", "estimate"] | None = None
    points: int | None = Field(
        default=None,
        ge=-100,
        le=100,
        description="Вклад ответа в итог (scale=weighted): сила с учётом срока и того, "
        "подтверждён ли канал данными компании",
    )
    features: list[ImpactFeature] = Field(
        default_factory=list,
        description="Признаки компании, через которые канал у неё есть; пусто — канал "
        "данными компании не подтверждён",
    )
    weight: ImpactWeight | None = Field(
        default=None, description="Расчёт вклада ответа по шагам (scale=weighted)"
    )

    @model_validator(mode="after")
    def answered(self) -> Self:
        if self.level == 0:
            raise ValueError("В ответах только вопросы с влиянием")
        if self.points is not None and self.points * self.level < 0:
            raise ValueError("Знак вклада ответа не совпадает с его уровнем")
        check_timing(self.level, self.timing, self.timing_title, self.timing_source)
        return self


def strongest(answers: list[ImpactAnswer]) -> ImpactAnswer | None:
    """Самый сильный ответ группы; при равной силе — риск, а не возможность."""
    return min(answers, key=lambda answer: (-abs(answer.level), answer.level), default=None)


class ImpactAssessment(Contract):
    """Оценка влияния события на бизнес компании: балл от −100 до +100, цвет и обоснование.
    Балл равен сумме вкладов критериев, ограниченной ±100; при scale=weighted — разнице
    возможностей и рисков."""

    score: int = Field(ge=-100, le=100)
    color: ImpactColor
    title: Text = Field(
        description="Сильный риск, Риск, Нейтрально, Возможность, Сильная возможность; "
        "Двоякое влияние — плюсы и минусы в сумме дали ноль"
    )
    criteria: list[ImpactCriterion] = Field(
        min_length=5,
        max_length=len(ImpactCriterionId),
        description="Все критерии оценки; в сохранённых до расширения оценках их пять",
    )
    rationale: Text = Field(description="Обоснование балла для карточки")
    method: Literal["llm", "rules"] = Field(
        description="llm — оценила модель с учётом профиля, rules — запасная оценка по теме"
    )
    confidence: Literal["low", "medium", "high"]
    model: Text | None = None
    answers: list[ImpactAnswer] = Field(
        default_factory=list,
        max_length=50,
        description="Вопросы с влиянием: уровень от −2 до +2 и вклад в итог",
    )
    scale: Literal["weighted", "levels", "points"] = Field(
        default="points",
        description="weighted — вклад ответа зависит от уровня, срока и подтверждения "
        "данными компании, риски и возможности накапливаются с насыщением до 100; "
        "levels и points — прежние подсчёты у сохранённых раньше оценок",
    )
    risk: int | None = Field(
        default=None, ge=0, le=100, description="Сила рисков 0–100 (scale=weighted)"
    )
    opportunity: int | None = Field(
        default=None, ge=0, le=100, description="Сила возможностей 0–100 (scale=weighted)"
    )

    @model_validator(mode="after")
    def criteria_from_answers(self) -> Self:
        require_unique((answer.question_id for answer in self.answers), "ответов")
        if self.scale == "weighted":
            self.check_weighted()
            return self
        for criterion in self.criteria:
            group = [a for a in self.answers if a.criterion is criterion.id]
            if self.scale == "levels":
                points = sum(a.level for a in group) if self.answers else criterion.level
                if criterion.points != points:
                    raise ValueError("Вклад критерия не равен сумме уровней его вопросов")
                if criterion.level != max(-2, min(2, points)):
                    raise ValueError("Уровень критерия не соответствует сумме его вопросов")
            else:
                if criterion.points != LEVEL_POINTS[criterion.level]:
                    raise ValueError("Вклад критерия не соответствует уровню")
                top = strongest(group)
                if self.answers and criterion.level != (top.level if top else 0):
                    raise ValueError("Уровень критерия не равен самому сильному ответу группы")
        return self

    def check_weighted(self) -> None:
        if self.risk is None or self.opportunity is None:
            raise ValueError("У взвешенной оценки есть сила рисков и возможностей")
        if self.score != self.opportunity - self.risk:
            raise ValueError("Балл влияния не равен разнице возможностей и рисков")
        if any(answer.points is None for answer in self.answers):
            raise ValueError("У каждого ответа взвешенной оценки есть вклад")
        parts = [answer.points or 0 for answer in self.answers] or [
            criterion.points for criterion in self.criteria
        ]
        if sum(p for p in parts if p < 0) != -self.risk or sum(p for p in parts if p > 0) != (
            self.opportunity
        ):
            raise ValueError("Вклады не складываются в силу рисков и возможностей")
        for criterion in self.criteria:
            group = [a.points or 0 for a in self.answers if a.criterion is criterion.id]
            if self.answers and criterion.points != sum(group):
                raise ValueError("Вклад критерия не равен сумме вкладов его вопросов")

    @model_validator(mode="after")
    def score_from_criteria(self) -> Self:
        require_unique((criterion.id.value for criterion in self.criteria), "критериев")
        total = max(-100, min(100, sum(criterion.points for criterion in self.criteria)))
        if total != self.score:
            raise ValueError("Балл влияния не равен сумме вкладов критериев")
        return self


class ImpactBand(Contract):
    """Цветовая зона шкалы влияния для легенды в интерфейсе."""

    color: ImpactColor
    title: Text
    min_score: int = Field(ge=-100, le=100)
    max_score: int = Field(ge=-100, le=100)
    hex: Text = Field(pattern=r"^#[0-9A-F]{6}$")


class ImpactCriterionInfo(Contract):
    id: ImpactCriterionId
    title: Text
    description: Text
