"""Проблемы материалов (A3-09): битая кодировка, пустая страница, платный доступ, нет даты,
ошибка обработки. Каждая записывается в лог и в таблицу news_issues, а обработка идёт
дальше: один плохой материал не останавливает ленту (ограничение 05 кейса).
"""

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from selectolax.parser import HTMLParser

IssueKind = Literal["encoding_fixed", "encoding", "empty_page", "paywall", "no_date", "error"]
MAX_ERRORS = 3
CHARSET = re.compile(rb"""<meta[^>]+charset=["']?([\w-]+)""", re.IGNORECASE)
HEADER_CHARSET = re.compile(r"charset=([\w-]+)", re.IGNORECASE)
CYRILLIC = re.compile(r"[а-яё]", re.IGNORECASE)
PAYWALL = re.compile(
    r"(доступ(?:ен|на|но)? только (?:для )?(?:подписчик|по подписке)|"
    r"(?:оформите подписку|подпишитесь),? чтобы (?:прочитать|дочитать|продолжить чтение)|"
    r"чтобы (?:прочитать|дочитать) (?:статью|материал) (?:полностью|целиком)|"
    r"(?:материал|статья) доступ(?:ен|на) (?:только )?по подписке|"
    r"полная версия (?:статьи )?доступна (?:только )?(?:подписчикам|по подписке)|"
    r"subscribe to (?:read|continue))",
    re.IGNORECASE,
)

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS news_issues (
    id bigserial PRIMARY KEY,
    raw_id text,
    news_id text,
    stage text NOT NULL,
    kind text NOT NULL,
    detail text,
    created_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS news_issues_raw ON news_issues (raw_id, kind);
"""
INSERT_SQL = """
INSERT INTO news_issues (raw_id, news_id, stage, kind, detail, created_at)
VALUES ($1, $2, $3, $4, $5, $6)
"""


@dataclass(frozen=True)
class Issue:
    kind: IssueKind
    detail: str


def decode_html(content: bytes, content_type: str | None = None) -> str:
    """Страница в текст: кодировка из заголовка, иначе из <meta charset>, иначе UTF-8,
    а если это не UTF-8 — windows-1251, частая у русских сайтов без указания кодировки."""
    candidates: list[str] = []
    header = HEADER_CHARSET.search(content_type or "")
    if header:
        candidates.append(header.group(1))
    meta = CHARSET.search(content[:4096])
    if meta:
        candidates.append(meta.group(1).decode("ascii", errors="ignore"))
    for encoding in [*candidates, "utf-8"]:
        try:
            return content.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return content.decode("cp1251", errors="replace")


def repair(text: str | None) -> tuple[str | None, Issue | None]:
    """Кракозябры от неверно угаданной кодировки: UTF-8, прочитанный как windows-1251
    («РЎР±РµСЂ») или как latin-1 («Ð¡Ð±ÐµÑ€»). Обратное преобразование принимается, только
    если оно проходит без ошибок и даёт кириллицу — нормальный текст так не испортится."""
    if not text or text.isascii():
        return text, None
    for wrong in ("cp1251", "latin-1"):
        try:
            fixed = text.encode(wrong).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        if CYRILLIC.search(fixed) and fixed != text:
            return fixed, Issue("encoding_fixed", f"кодировка исправлена: {wrong} → utf-8")
    if "�" in text:
        return text, Issue("encoding", "в тексте символы, которые не удалось декодировать")
    return text, None


def visible_text(html: str) -> str:
    tree = HTMLParser(html)
    for node in tree.css("script, style, noscript, template"):
        node.decompose()
    root = tree.body or tree.root
    return root.text(separator=" ") if root is not None else ""


def paywalled(html: str | None) -> bool:
    """Платный доступ: в видимом тексте страницы фраза «подпишитесь, чтобы дочитать» и похожие.
    Ищем в видимом тексте, а не в HTML: слово paywall есть в скриптах и классах многих сайтов
    и на бесплатных статьях. Реклама подписки («оформите подписку и читайте без рекламы») —
    не платный доступ."""
    return bool(html) and PAYWALL.search(visible_text(html or "")) is not None


async def ensure_schema(connection: Any) -> None:
    await connection.execute(SCHEMA)


async def record(
    connection: Any,
    stage: str,
    issue: Issue,
    *,
    raw_id: str | None = None,
    news_id: str | None = None,
    now: datetime | None = None,
) -> None:
    level = logging.WARNING if issue.kind in ("error", "encoding") else logging.INFO
    logger.log(level, "%s: %s %s — %s", stage, raw_id or news_id or "", issue.kind, issue.detail)
    await connection.execute(
        INSERT_SQL,
        raw_id,
        news_id,
        stage,
        issue.kind,
        issue.detail[:1000],
        now or datetime.now(UTC),
    )


def failure(error: BaseException) -> Issue:
    return Issue("error", f"{type(error).__name__}: {error}"[:1000])
