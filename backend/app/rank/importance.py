"""Степень важности (A4-08): балл 0–10, уровень и вклад каждого фактора — ответ на вопрос
«почему 7,3».

Балл — сумма шести факторов с потолками, потолки вместе дают ровно 10:

    связь с компанией        4,0   балл правил (A4-01) с учётом штрафов
    масштаб события          2,0   тема, охват изданий, подтверждённые цифры (A4-02)
    свежесть                 1,5   полураспад по горизонту влияния (A4-02)
    затронуто сторон бизнеса 1,0   сколько разных правил сработало
    подтверждение моделью    1,0   оценка проверки кандидатов (A4-03); без модели — 0
    позиция Альфа-Банка      0,5   материал аналитики банка

Связь с компанией весит больше всего. Масштаб и свежесть начисляются в меру связи: целиком
при совпадении не слабее, чем по своему рынку (0,6), при слабой связи — пропорционально, а
без связи остальные факторы не начисляются вовсе. Громкая и свежая новость, которая едва
касается бизнеса, не должна обгонять ту, что касается его прямо. Уровни и их
критерии отдаются в ленте, чтобы пользователь видел, что значит «критично». Порог отсечения
настраивается: FEED_MIN_IMPORTANCE и параметр запроса; по умолчанию 3,5 — граница «Фона»,
подобрана на эталонной разметке A4-06 и проверена на отложенной A4-07 (eval/).
"""

from app.contracts.news import MaterialKind
from app.contracts.rank import Importance, ImportanceFactor, ImportanceLevel, ImportanceScale
from app.rank.rules import Rule
from app.rank.score import Scored

RELEVANCE_MAX = 4.0
SCALE_MAX = 2.0
RECENCY_MAX = 1.5
BREADTH_MAX = 1.0
MODEL_MAX = 1.0
BANK_MAX = 0.5
BREADTH_STEP = 0.5
MIN_RELEVANCE = 0.1
FULL_LINK = 0.6
NO_LINK = "Не учитывается: нет связи с компанией"
DEFAULT_MIN_IMPORTANCE = 3.5
LEVELS = (
    (
        ImportanceLevel.CRITICAL,
        7.5,
        "Критично",
        "Прямо затрагивает бизнес компании через несколько её признаков, событие крупное "
        "и свежее: стоит разобрать сегодня",
    ),
    (
        ImportanceLevel.IMPORTANT,
        5.5,
        "Важно",
        "Заметно касается компании: стоит учесть в ближайших решениях",
    ),
    (
        ImportanceLevel.INFO,
        3.5,
        "К сведению",
        "Связь с компанией есть, но косвенная или событие небольшое: полезно знать",
    ),
    (
        ImportanceLevel.BACKGROUND,
        0.0,
        "Фон",
        "Слабая связь с компанией: в ленте только при сниженном пороге",
    ),
)
RULE_TITLES = {
    Rule.INDUSTRY: "отрасль",
    Rule.COUNTERPARTY: "контрагенты",
    Rule.GROUP: "группа и связанные компании",
    Rule.REGION: "регион",
    Rule.PRODUCT: "продукты банка",
    Rule.EXPOSURE: "чувствительность к теме",
    Rule.ENTITY: "конкуренты",
    Rule.SCALE: "размер бизнеса",
    Rule.FOREIGN_TRADE: "ВЭД",
}


RULE_VALUES = frozenset(rule.value for rule in Rule)


def level_of(score: float) -> tuple[ImportanceLevel, str]:
    for level, minimum, title, _ in LEVELS:
        if score >= minimum:
            return level, title
    return ImportanceLevel.BACKGROUND, "Фон"


def scale() -> list[ImportanceScale]:
    return [
        ImportanceScale(level=level, title=title, min_score=minimum, description=description)
        for level, minimum, title, description in LEVELS
    ]


def points(value: float, maximum: float) -> float:
    return round(max(0.0, min(value, 1.0)) * maximum, 1)


def touched(scored: Scored) -> list[str]:
    rules = [Rule(found.rule) for found in scored.contributions if found.rule in RULE_VALUES]
    return [RULE_TITLES[rule] for rule in dict.fromkeys(rules)]


def importance(scored: Scored, kind: MaterialKind, llm: float | None = None) -> Importance:
    sides = touched(scored)
    main = scored.contributions[0].reason if scored.contributions else "совпадений нет"
    relevance_detail = f"Совпадение с признаками компании {scored.relevance * 100:.0f}%: {main}"
    if scored.role is not None:
        relevance_detail += ". " + scored.role.detail
    if scored.penalties:
        relevance_detail += ". Штраф: " + "; ".join(scored.penalties)
    breadth = min(BREADTH_MAX, BREADTH_STEP * max(0, len(sides) - 1))
    gate = min(1.0, scored.relevance / FULL_LINK)
    damped = f". Учтено в меру связи с компанией: {gate * 100:.0f}%" if gate < 1 else ""
    factors = [
        ImportanceFactor(
            id="relevance",
            title="Связь с компанией",
            points=points(scored.relevance, RELEVANCE_MAX),
            max_points=RELEVANCE_MAX,
            detail=relevance_detail,
        ),
        ImportanceFactor(
            id="scale",
            title="Масштаб события",
            points=points(scored.scale * gate, SCALE_MAX),
            max_points=SCALE_MAX,
            detail=(scored.scale_note or "Масштаб не оценён") + damped,
        ),
        ImportanceFactor(
            id="recency",
            title="Свежесть",
            points=points(scored.recency * gate, RECENCY_MAX),
            max_points=RECENCY_MAX,
            detail=(scored.recency_note or "Дата публикации не учтена") + damped,
        ),
        ImportanceFactor(
            id="breadth",
            title="Затронуто сторон бизнеса",
            points=round(breadth, 1),
            max_points=BREADTH_MAX,
            detail=f"Задето: {', '.join(sides)}" if sides else "Ни одна сторона бизнеса не задета",
        ),
        ImportanceFactor(
            id="model",
            title="Подтверждение моделью",
            points=points(llm, MODEL_MAX) if llm is not None else 0.0,
            max_points=MODEL_MAX,
            detail=f"Модель подтвердила связь с компанией: оценка {llm * 100:.0f}%"
            if llm is not None
            else "Модель не проверяла: балл без этого фактора",
        ),
        ImportanceFactor(
            id="bank",
            title="Позиция Альфа-Банка",
            points=BANK_MAX if kind is MaterialKind.BANK_ANALYTICS else 0.0,
            max_points=BANK_MAX,
            detail="Материал аналитики Альфа-Банка"
            if kind is MaterialKind.BANK_ANALYTICS
            else "Внешний источник, не позиция банка",
        ),
    ]
    if scored.relevance < MIN_RELEVANCE:
        factors = [
            factors[0],
            *(
                factor.model_copy(update={"points": 0.0, "detail": NO_LINK})
                for factor in factors[1:]
            ),
        ]
    total = round(sum(factor.points for factor in factors), 1)
    level, title = level_of(total)
    return Importance(score=total, level=level, title=title, factors=factors)
