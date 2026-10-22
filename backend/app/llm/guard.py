"""A5-10: текст источника — данные, а не инструкция.

Перед тем как попасть в промпт, внешний текст (новость, аналитика, правки клиента, запрос)
проходит фильтр: подделки служебной разметки промпта обезвреживаются, предложения с командами
для модели скрываются. Обычный деловой текст остаётся без изменений. Фильтр — второй рубеж:
первый — инструкция «данные, а не команды» и маркеры блоков, третий — строгая схема ответа,
закрытые списки и проверка ссылок на источники.
"""

import re
from dataclasses import dataclass
from typing import Any, Literal

import structlog
from pydantic import JsonValue

type Threat = Literal["delimiter", "chat_token", "role", "block_id", "instruction", "schema"]

REDACTED = "[фрагмент скрыт фильтром: похоже на команду для модели]"
TRUSTED_KEYS = frozenset({"output_schema", "tools", "budget"})
DELIMITER = re.compile(r"<{3,}|>{3,}")
CHAT_TOKEN = re.compile(
    r"<\|[^|<>\n]{1,40}\|>|\[/?INST\]|<</?SYS>>|</?s>|<(?:start|end)_of_turn>",
    re.IGNORECASE,
)
ROLE = re.compile(
    r"^(\s*[#>*\-]*\s*)(system|assistant|developer|user|tool|система|ассистент|"
    r"пользователь|инструкция для (?:модели|ии|ai))(\s*:)",
    re.IGNORECASE | re.MULTILINE,
)
BLOCK_ID = re.compile(r"^(\s*)\[((?:n|c|p)\d{1,3})\]", re.IGNORECASE | re.MULTILINE)
SCHEMA_KEY = re.compile(
    r"\"(verdicts|ranking|assessments|facts|topics|markers|relevant|action|tool|output|"
    r"bank_view|issues)\"(\s*:)"
)
INSTRUCTION = re.compile(
    r"\b(?:ignore|disregard|forget|override|bypass)\s+(?:all\s+)?(?:(?:the|your|any)\s+)?"
    r"(?:previous|prior|above|earlier|preceding|system|safety|all)\s+"
    r"(?:instructions?|rules|prompts?|guidelines)"
    r"|\b(?:ignore|disregard)\s+(?:all\s+)?(?:(?:the|your|any)\s+)?(?:instructions|prompts?)\b"
    r"|\byou\s+are\s+now\s+(?:an?\s+)?(?:assistant|ai|model|unrestricted|dan|jailbroken)"
    r"|\bnew\s+instructions?\s*:|\b(?:reveal|print|show)\s+(?:your\s+|the\s+)?system\s+prompt"
    r"|(?<![\w-])(?:проигнорируй|игнорируй|забудь|не\s+учитывай|не\s+следуй|не\s+выполняй)(?:те)?"
    r"\s+(?:(?:все|всё|любые|свои|твои|эти)\s+)?(?:(?:предыдущ|прежн|вышеуказанн|системн|"
    r"исходн)\w*\s+(?:инструкци|указани|правил|промпт|ограничени)\w*|(?:инструкци|промпт)\w*)"
    r"|(?<![\w-])теперь\s+ты\s+(?:—\s*)?(?:не\s+)?(?:ассистент|модель|ии|бот|агент)"
    r"|(?<![\w-])(?:новые|следующие)\s+инструкции\s*:"
    r"|(?<![\w-])(?:покажи|выведи|раскрой)(?:те)?\s+(?:свой\s+|твой\s+)?системн\w+\s+промпт"
    r"|(?<![\w-])(?:для|к)\s+(?:языковой\s+)?(?:модел|нейросет|ии\b)\w*\s*:"
    r"|(?<![\w-])(?:верни|отметь|поставь|оцени|присвой)(?:те)?\s+(?:эт\w+\s+)?"
    r"(?:новост|материал|стать)\w*\s+(?:как\s+|оценку\s+)?(?:релевантн|важн|высок|максимальн)\w*",
    re.IGNORECASE,
)
SENTENCE_END = re.compile(r"[.!?…]|\n")

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class Guarded:
    text: str
    threats: tuple[Threat, ...] = ()


def redact_instructions(text: str) -> tuple[str, bool]:
    found = False
    while match := INSTRUCTION.search(text):
        found = True
        before = text[: match.start()]
        start = max((m.end() for m in SENTENCE_END.finditer(before)), default=0)
        after = SENTENCE_END.search(text, match.end())
        end = after.end() if after else len(text)
        prefix = " " if start and not text[start - 1].isspace() else ""
        suffix = "\n" if after and after.group() == "\n" else " " if end < len(text) else ""
        text = text[:start] + prefix + REDACTED + suffix + text[end:].lstrip(" ")
    return text, found


def inspect(text: str) -> Guarded:
    threats: list[Threat] = []

    def mark(threat: Threat, changed: str, original: str) -> str:
        if changed != original:
            threats.append(threat)
        return changed

    text = mark("chat_token", CHAT_TOKEN.sub(" ", text), text)
    text = mark(
        "delimiter", DELIMITER.sub(lambda m: "‹‹‹" if m.group()[0] == "<" else "›››", text), text
    )
    text = mark("role", ROLE.sub(r"\1«\2»\3", text), text)
    text = mark("block_id", BLOCK_ID.sub(r"\1(\2)", text), text)
    text = mark("schema", SCHEMA_KEY.sub(r"«\1»\2", text), text)
    text, instructed = redact_instructions(text)
    if instructed:
        threats.append("instruction")
    return Guarded(text, tuple(threats))


def scan(text: str) -> tuple[Threat, ...]:
    return inspect(text).threats


def clean(text: str, where: str = "источник") -> str:
    guarded = inspect(text)
    if guarded.threats:
        logger.warning(
            "В тексте источника обезврежены признаки инъекции",
            where=where,
            threats=list(guarded.threats),
        )
    return guarded.text


def clean_json(value: JsonValue, where: str = "контекст") -> JsonValue:
    if isinstance(value, str):
        return clean(value, where)
    if isinstance(value, list):
        return [clean_json(item, where) for item in value]
    if isinstance(value, dict):
        return {key: clean_json(item, where) for key, item in value.items()}
    return value


def guard_context(data: dict[str, Any], where: str = "контекст") -> dict[str, Any]:
    return {
        key: value if key in TRUSTED_KEYS else clean_json(value, where)
        for key, value in data.items()
    }
