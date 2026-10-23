import re
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from app.profile.okved import normalize_code

NULL_TOKENS = {"", "nan", "none", "null", "nat", "-", "—", "–", "н/д", "нет данных", "#n/a", "n/a"}
TRUE_TOKENS = {"1", "1.0", "true", "t", "да", "yes", "y"}
FALSE_TOKENS = {"0", "0.0", "false", "f", "нет", "no", "n"}
SPACES = re.compile(r"[\s   ]+")
CURRENCY = re.compile(r"(руб\.?|₽|rub)$", re.IGNORECASE)
COUNTRY_SEPARATORS = re.compile(r"[,;/]")
MAX_EXAMPLE_LINES = 5
REVENUE_STUB = Decimal("1000.00")


@dataclass
class Issues:
    """Счётчик проблем в данных: что и сколько раз, с номерами первых строк, без значений."""

    counts: Counter[tuple[str, str, str]] = field(default_factory=Counter)
    lines: dict[tuple[str, str, str], list[int]] = field(default_factory=dict)

    def add(self, table: str, column: str, problem: str, line: int) -> None:
        key = (table, column, problem)
        self.counts[key] += 1
        examples = self.lines.setdefault(key, [])
        if len(examples) < MAX_EXAMPLE_LINES:
            examples.append(line)

    def summary(self) -> list[dict[str, object]]:
        return [
            {
                "table": table,
                "column": column,
                "problem": problem,
                "count": count,
                "lines": self.lines[(table, column, problem)],
            }
            for (table, column, problem), count in sorted(self.counts.items())
        ]


@dataclass(frozen=True)
class Cell:
    """Значение из строки CSV вместе с тем, откуда оно пришло: для учёта проблем."""

    raw: str | None
    table: str
    column: str
    line: int
    issues: Issues

    def problem(self, text: str) -> None:
        self.issues.add(self.table, self.column, text, self.line)


def text(cell: Cell) -> str | None:
    if cell.raw is None:
        return None
    value = SPACES.sub(" ", cell.raw).strip().strip('"').strip()
    return None if value.lower() in NULL_TOKENS else value


def mark(cell: Cell) -> bool | None:
    value = text(cell)
    if value is None:
        return None
    lowered = value.lower().replace(",", ".")
    if lowered in TRUE_TOKENS:
        return True
    if lowered in FALSE_TOKENS:
        return False
    cell.problem("флаг не распознан")
    return None


def number(cell: Cell) -> Decimal | None:
    value = text(cell)
    if value is None:
        return None
    value = CURRENCY.sub("", value.replace(" ", "")).strip()
    if "," in value and "." in value:
        decimal_separator = "," if value.rfind(",") > value.rfind(".") else "."
        thousands = "." if decimal_separator == "," else ","
        value = value.replace(thousands, "").replace(decimal_separator, ".")
    else:
        value = value.replace(",", ".")
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        cell.problem("не число")
        return None
    if not parsed.is_finite():
        cell.problem("не число")
        return None
    return parsed


def amount(cell: Cell) -> Decimal | None:
    parsed = number(cell)
    if parsed is None:
        return None
    if parsed < 0:
        cell.problem("отрицательное значение")
        return None
    if parsed == 0:
        cell.problem("нулевое значение")
    return parsed.quantize(Decimal("0.01"))


def revenue(cell: Cell) -> Decimal | None:
    parsed = amount(cell)
    if parsed == REVENUE_STUB:
        cell.problem("заглушка 1000 ₽ вместо выручки, считаем неизвестной")
        return None
    return parsed


def count(cell: Cell) -> int | None:
    parsed = number(cell)
    if parsed is None:
        return None
    if parsed < 0 or parsed != parsed.to_integral_value():
        cell.problem("не целое неотрицательное")
        return None
    return int(parsed)


def okved(cell: Cell) -> str | None:
    value = text(cell)
    if value is None:
        return None
    code = normalize_code(value)
    if code is None:
        cell.problem("код ОКВЭД не распознан")
    return code


def choice(cell: Cell, allowed: set[str]) -> str | None:
    value = text(cell)
    if value is None:
        return None
    normalized = value.upper().replace(" ", "_")
    if normalized not in allowed:
        cell.problem("значение вне справочника")
        return None
    return normalized


def countries(cell: Cell) -> list[str]:
    value = text(cell)
    if value is None:
        return []
    parts = (part.strip().upper() for part in COUNTRY_SEPARATORS.split(value))
    return list(dict.fromkeys(part for part in parts if part and part.lower() not in NULL_TOKENS))
