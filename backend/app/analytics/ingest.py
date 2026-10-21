"""Аналитика Альфа-Банка (A3-06): PDF-материалы Центра макроэкономического анализа
превращаются в NewsItem с kind=bank_analytics — текст фрагментами со ссылкой на страницу,
метаданные (дата, тема, авторы, вывод) и позиция банка отдельными фактами.

Материалы кейса — сканы без текстового слоя, поэтому текст распознаёт Tesseract (классический
OCR, не генеративная модель; бюджет LLM не тратится). Если в PDF есть текстовый слой, берём
его. Распознанные страницы кешируются по хешу файла: повторная загрузка не распознаёт заново.

Позиция банка — предложения от первого лица («мы ожидаем», «наш прогноз»): они становятся
key_facts со статусом forecast или assumption и ссылкой на фрагмент. Так интерфейс и агент
отличают позицию банка от пересказа данных и от выводов модели (критерий 03).
"""

import calendar
import hashlib
import logging
import re
import struct
import subprocess
import zlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, cast

from pypdf import PdfReader
from pypdf.errors import PdfReadError
from pypdf.generic import DictionaryObject, StreamObject

from app.contracts.news import (
    Channel,
    Classification,
    Dedup,
    FactStatus,
    Fragment,
    KeyFact,
    MaterialKind,
    NewsItem,
    Source,
    Topic,
)
from app.core.config import get_settings

SOURCE_NAME = "Центр макроэкономического анализа Альфа-Банка"
MOSCOW = timezone(timedelta(hours=3))
MIN_FRAGMENT = 60
MAX_FRAGMENTS = 200
MAX_BANK_VIEW = 12
OCR_TIMEOUT = 180
MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}  # fmt: skip
EN_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}  # fmt: skip
DATE_TEXT = re.compile(r"\b(\d{1,2})\s+(" + "|".join(MONTHS) + r")\s+(20\d\d)", re.IGNORECASE)
DATE_DIGITS = re.compile(r"(?<!\d)(\d{2})(\d{2})(20\d\d)(?!\d)")
DATE_EN = re.compile(r"(\d{1,2})(" + "|".join(EN_MONTHS) + r")[a-z]*(20\d\d)", re.IGNORECASE)
MONTHS_NOMINATIVE = {
    "январь": 1, "февраль": 2, "март": 3, "апрель": 4, "май": 5, "июнь": 6,
    "июль": 7, "август": 8, "сентябрь": 9, "октябрь": 10, "ноябрь": 11, "декабрь": 12,
}  # fmt: skip
MONTH_YEAR = re.compile(r"\b(" + "|".join(MONTHS_NOMINATIVE) + r")\s+(20\d\d)", re.IGNORECASE)
EMAIL = re.compile(r"\S+@\S+")
NAME = re.compile(r"^[А-ЯЁ][а-яё]+ [А-ЯЁ][а-яё]+,?$")
SURNAME = r"[А-ЯЁ][а-яё]+(?:ова|ева|ина|ов|ев|ин|ак|як|ко|ых|их|ий|ая|ой|ик|ук|юк|ец)"
HEADER_LINES = 30
TRAILING_NAME = re.compile(rf"\s*\b[А-ЯЁ][а-яё]+ {SURNAME},?\s*$")
NOISE = re.compile(
    r"^(центр макроэкономического анализа|альфа-банк|alfa|www\.|стр\.|страница|\d+$)",
    re.IGNORECASE,
)
DISCLAIMER = re.compile(
    r"(все права защищены|генеральная лицензия|несанкционированное копирование|"
    r"не является деятельностью по инвестиционному|исключительной собственностью)",
    re.IGNORECASE,
)
BANK_VIEW = re.compile(
    r"\b(мы ожидаем|мы полагаем|мы считаем|мы прогнозируем|мы сохраняем|мы меняем|"
    r"мы понизили|мы повысили|мы ориентируемся|мы рекомендуем|наш прогноз|наш взгляд|"
    r"наша оценка|по нашим оценкам|по нашему мнению|мы не исключаем)",
    re.IGNORECASE,
)
FORECAST = re.compile(r"\b(ожида|прогноз|ориентиру)", re.IGNORECASE)
CAPTION = re.compile(r"^(рис\.?|рисунок|график|таблица|табл\.|источник|примечание)\s*\d*", re.I)
CAPTION_LIMIT = 250
SENTENCE_END = re.compile(r"[.!?…:;»)\]]$")
SENTENCE = re.compile(r"(?<=[.!?…])\s+(?=[А-ЯЁA-Z«\"])")
THEMES: dict[Topic, tuple[str, ...]] = {
    Topic.KEY_RATE: ("ключев", "ставк", "дкп"),
    Topic.INFLATION: ("инфляц", "цен "),
    Topic.DEBT_MARKET: ("облигац", "офз", "бонд", "доходност"),
    Topic.MACRO: ("ввп", "макростатистик", "промпроизводств", "бюджет"),
    Topic.CONSUMPTION: ("потреблен", "спрос", "розничн"),
    Topic.LABOR: ("рынок труда", "рынка труда", "безработ", "зарплат"),
    Topic.COMMODITIES: ("золот", "сталь", "нефт", "металл", "сырь", "commodit"),
    Topic.FX: ("курс рубл", "рубль", "валют"),
}

logger = logging.getLogger(__name__)


class IngestError(Exception):
    pass


def png(width: int, height: int, channels: int, raw: bytes) -> bytes:
    """PNG из сырых пикселей страницы: Tesseract читает PNG, а Pillow ради этого не нужен."""
    stride = width * channels
    rows = b"".join(b"\x00" + raw[row * stride : (row + 1) * stride] for row in range(height))
    color = {1: 0, 3: 2, 4: 6}[channels]

    def chunk(kind: bytes, data: bytes) -> bytes:
        crc = struct.pack(">I", zlib.crc32(kind + data))
        return struct.pack(">I", len(data)) + kind + data + crc

    header = struct.pack(">IIBBBBB", width, height, 8, color, 0, 0, 0)
    body = chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows, 6)) + chunk(b"IEND", b"")
    return b"\x89PNG\r\n\x1a\n" + body


def page_images(reader: PdfReader, number: int) -> list[bytes]:
    """Картинки страницы в формате, который понимает Tesseract: JPEG как есть, остальное — PNG."""
    images: list[bytes] = []
    resources = cast(DictionaryObject, reader.pages[number]["/Resources"].get_object())
    objects = cast(DictionaryObject, resources.get("/XObject", DictionaryObject()).get_object())
    for reference in objects.values():
        image = cast(StreamObject, reference.get_object())
        if image.get("/Subtype") != "/Image":
            continue
        if image.get("/Filter") == "/DCTDecode":
            images.append(image.get_data())
            continue
        width, height = cast(int, image["/Width"]), cast(int, image["/Height"])
        data = image.get_data()
        channels = len(data) // (width * height)
        if channels in (1, 3, 4) and image.get("/BitsPerComponent", 8) == 8:
            images.append(png(width, height, channels, data))
    return images


def tesseract(image: bytes, command: str, languages: str) -> str:
    try:
        result = subprocess.run(  # noqa: S603
            [command, "stdin", "stdout", "-l", languages, "--psm", "3"],
            input=image,
            capture_output=True,
            timeout=OCR_TIMEOUT,
            check=True,
        )
    except FileNotFoundError as error:
        raise IngestError(f"OCR не найден: {command}. Установите tesseract-ocr-rus") from error
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise IngestError(f"OCR не справился со страницей: {error}") from error
    return result.stdout.decode("utf-8", errors="replace")


@dataclass(frozen=True)
class Page:
    number: int
    text: str
    engine: str


def clean_line(line: str) -> str:
    return re.sub(r"\s+", " ", line.replace("\xa0", " ")).strip()


def is_noise(line: str) -> bool:
    letters = sum(char.isalpha() for char in line)
    return (
        not line
        or bool(EMAIL.search(line))
        or bool(NAME.match(line))
        or bool(NOISE.match(line))
        or letters < 3
        or letters / len(line) < 0.5
    )


def paragraphs(text: str) -> list[str]:
    """Абзацы распознанной страницы: строки склеиваются, после дефиса в конце строки — без
    пробела («пресс-релиз»). Подписи графиков, номера страниц, контакты и дисклеймер
    отбрасываются."""
    blocks: list[str] = []
    for block in re.split(r"\n\s*\n", text):
        lines = [clean_line(line) for line in block.splitlines()]
        lines = [line for line in lines if line and not is_noise(line)]
        joined = ""
        for line in lines:
            if joined.endswith("-") and line[:1].islower():
                joined += line
            else:
                joined = f"{joined} {line}".strip()
        if joined and not DISCLAIMER.search(joined) and not is_caption(joined):
            blocks.append(joined)
    return blocks


def is_caption(text: str) -> bool:
    """Подпись графика или таблицы: «Рис. 5: …», «Источник: …» или короткий обрывок без точки
    в конце, как OCR читает легенду диаграммы."""
    return bool(CAPTION.match(text)) or (
        len(text) < CAPTION_LIMIT and not SENTENCE_END.search(text)
    )


def document_date(pages: Sequence[Page], file_name: str) -> date | None:
    head = " ".join(page.text for page in pages[:1])[:3000]
    match = DATE_TEXT.search(head)
    if match:
        return date(int(match.group(3)), MONTHS[match.group(2).lower()], int(match.group(1)))
    digits = DATE_DIGITS.search(file_name)
    if digits:
        day, month, year = (int(value) for value in digits.groups())
        if 1 <= month <= 12 and 1 <= day <= 31:
            return date(year, month, day)
    english = DATE_EN.search(file_name)
    if english:
        month = EN_MONTHS[english.group(2).lower()[:3]]
        return date(int(english.group(3)), month, int(english.group(1)))
    monthly = MONTH_YEAR.search(head)
    if monthly:
        year, month = int(monthly.group(2)), MONTHS_NOMINATIVE[monthly.group(1).lower()]
        return date(year, month, calendar.monthrange(year, month)[1])
    return None


def title_line(raw: str) -> str | None:
    """Строка шапки как кандидат в заголовок. Шапка вёрстана в две колонки, и OCR склеивает
    заголовок с именем автора и почтой — их отрезаем. Остатки распознанной почты («гоу If
    aad») — одни короткие обрывки, такие строки не заголовок."""
    line = TRAILING_NAME.sub("", EMAIL.sub("", clean_line(raw))).strip(" ,_—")
    if (
        len(line) < 6
        or len(line) > 110
        or is_noise(line)
        or DATE_TEXT.search(line)
        or MONTH_YEAR.fullmatch(line)
        or all(len(token) <= 3 for token in line.split())
    ):
        return None
    return line


def headings(pages: Sequence[Page]) -> tuple[str, str | None]:
    """Заголовок и подзаголовок-вывод: первые содержательные короткие строки первой страницы,
    без шапки центра, дат, имён и контактов."""
    found: list[str] = []
    for raw in pages[0].text.splitlines() if pages else []:
        line = title_line(raw)
        if line is None:
            continue
        found.append(line)
        if len(found) == 2:
            break
    if not found:
        raise IngestError("Не найден заголовок материала")
    return found[0], found[1] if len(found) > 1 else None


def authors(pages: Sequence[Page]) -> list[str]:
    """Авторы из шапки: имя с фамилией в конце строки, как их ставит вёрстка центра."""
    names = []
    for raw in pages[0].text.splitlines()[:HEADER_LINES] if pages else []:
        match = TRAILING_NAME.search(EMAIL.sub("", clean_line(raw)))
        if match:
            names.append(match.group(0).strip(" ,"))
    return list(dict.fromkeys(names))


def themes(text: str) -> list[Topic]:
    lowered = text.lower()
    scored = [
        (sum(lowered.count(word) for word in words), topic) for topic, words in THEMES.items()
    ]
    ranked = [topic for score, topic in sorted(scored, key=lambda pair: -pair[0]) if score >= 2]
    return ranked[:4] or [Topic.MACRO]


def bank_view(fragments: Sequence[Fragment]) -> list[KeyFact]:
    """Позиция банка: предложения от первого лица со ссылкой на фрагмент."""
    facts: list[KeyFact] = []
    for fragment in fragments:
        for sentence in SENTENCE.split(fragment.text):
            if not BANK_VIEW.search(sentence) or len(facts) >= MAX_BANK_VIEW:
                continue
            status = FactStatus.FORECAST if FORECAST.search(sentence) else FactStatus.ASSUMPTION
            facts.append(
                KeyFact(
                    id=f"b{len(facts) + 1}",
                    text=sentence.strip(),
                    status=status,
                    fragment_id=fragment.id,
                )
            )
    return facts


def content_hash(text: str) -> str:
    words = re.findall(r"\w+", text.lower())
    return hashlib.sha256(" ".join(words).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Document:
    item: NewsItem
    file_name: str
    sha256: str
    subtitle: str | None
    authors: list[str]
    pages: int
    text_source: str


def build(pages: Sequence[Page], file_name: str, sha256: str, collected_at: datetime) -> Document:
    """NewsItem из страниц материала: фрагменты — абзацы со ссылкой «стр. N, абзац M»."""
    title, subtitle = headings(pages)
    fragments: list[Fragment] = []
    for page in pages:
        index = 0
        for paragraph in paragraphs(page.text):
            if len(paragraph) < MIN_FRAGMENT or len(fragments) >= MAX_FRAGMENTS:
                continue
            index += 1
            fragments.append(
                Fragment(
                    id=f"s{page.number}-{index}",
                    text=paragraph,
                    locator=f"стр. {page.number}, абзац {index}",
                )
            )
    if not fragments:
        raise IngestError(f"В материале {file_name} не найден текст")
    text = "\n\n".join(fragment.text for fragment in fragments)
    published = document_date(pages, file_name)
    moment = (
        datetime.combine(published, time(18, 0), MOSCOW).astimezone(UTC)
        if published
        else collected_at
    )
    facts = bank_view(fragments)
    full_title = f"{title}: {subtitle}" if subtitle else title
    item = NewsItem(
        id=f"bank:{sha256[:16]}",
        kind=MaterialKind.BANK_ANALYTICS,
        source=Source(name=SOURCE_NAME, channel=Channel.PDF),
        title=full_title,
        published_at=moment,
        collected_at=collected_at,
        text=text,
        summary=subtitle,
        fragments=fragments,
        key_facts=facts,
        classification=Classification(
            topics=themes(f"{full_title} {full_title} {text}"),
            entities=["Альфа-Банк"],
            confidence=0.5,
        ),
        dedup=Dedup(content_hash=content_hash(text)),
    )
    engine = {page.engine for page in pages}
    return Document(
        item=item,
        file_name=file_name,
        sha256=sha256,
        subtitle=subtitle,
        authors=authors(pages),
        pages=len(pages),
        text_source="ocr" if "tesseract" in engine else "text",
    )


SCHEMA = """
CREATE TABLE IF NOT EXISTS analytics_pages (
    sha256 text NOT NULL,
    page integer NOT NULL,
    engine text NOT NULL,
    text text NOT NULL,
    PRIMARY KEY (sha256, page)
);
CREATE TABLE IF NOT EXISTS analytics_documents (
    doc_id text PRIMARY KEY,
    sha256 text NOT NULL UNIQUE,
    file_name text NOT NULL,
    title text NOT NULL,
    subtitle text,
    published_at timestamptz NOT NULL,
    authors text[] NOT NULL DEFAULT '{}',
    topics text[] NOT NULL DEFAULT '{}',
    pages integer NOT NULL,
    text_source text NOT NULL,
    item jsonb NOT NULL,
    ingested_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS analytics_documents_published ON analytics_documents (published_at);
"""
PAGES_SQL = "SELECT page, engine, text FROM analytics_pages WHERE sha256 = $1 ORDER BY page"
PAGE_SQL = """
INSERT INTO analytics_pages (sha256, page, engine, text) VALUES ($1, $2, $3, $4)
ON CONFLICT (sha256, page) DO UPDATE SET engine = EXCLUDED.engine, text = EXCLUDED.text
"""
DOCUMENT_SQL = """
INSERT INTO analytics_documents (doc_id, sha256, file_name, title, subtitle, published_at,
                                 authors, topics, pages, text_source, item, ingested_at)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb, $12)
ON CONFLICT (doc_id) DO UPDATE SET
    file_name = EXCLUDED.file_name, title = EXCLUDED.title, subtitle = EXCLUDED.subtitle,
    published_at = EXCLUDED.published_at, authors = EXCLUDED.authors, topics = EXCLUDED.topics,
    pages = EXCLUDED.pages, text_source = EXCLUDED.text_source, item = EXCLUDED.item,
    ingested_at = EXCLUDED.ingested_at
"""


@dataclass
class IngestReport:
    documents: list[dict[str, object]] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)


async def ensure_schema(connection: Any) -> None:
    await connection.execute(SCHEMA)


async def read_pages(connection: Any, path: Path, sha256: str) -> list[Page]:
    """Текст страниц: из кеша, из текстового слоя PDF или распознанный OCR."""
    cached = await connection.fetch(PAGES_SQL, sha256)
    try:
        reader = PdfReader(path)
        total = len(reader.pages)
    except (PdfReadError, OSError) as error:
        raise IngestError(f"PDF не прочитан: {error}") from error
    if len(cached) == total:
        return [Page(record["page"], record["text"], record["engine"]) for record in cached]
    settings = get_settings()
    pages: list[Page] = []
    for index in range(total):
        layer = (reader.pages[index].extract_text() or "").strip()
        if len(layer) > MIN_FRAGMENT:
            page = Page(index + 1, layer, "pdf_text")
        else:
            images = page_images(reader, index)
            text = "\n\n".join(
                tesseract(image, settings.ocr_command, settings.ocr_languages) for image in images
            )
            page = Page(index + 1, text, "tesseract")
        await connection.execute(PAGE_SQL, sha256, page.number, page.engine, page.text)
        pages.append(page)
    return pages


async def ingest_file(connection: Any, path: Path, now: datetime) -> Document:
    sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    pages = await read_pages(connection, path, sha256)
    document = build(pages, path.name, sha256, now)
    item = document.item
    classification = item.classification
    await connection.execute(
        DOCUMENT_SQL,
        item.id,
        sha256,
        path.name,
        item.title,
        document.subtitle,
        item.published_at,
        document.authors,
        [topic.value for topic in classification.topics] if classification else [],
        document.pages,
        document.text_source,
        item.model_dump_json(),
        now,
    )
    return document


async def ingest_dir(connection: Any, folder: Path, now: datetime | None = None) -> IngestReport:
    """Все PDF каталога. Ошибка одного материала не останавливает остальные."""
    await ensure_schema(connection)
    moment = now or datetime.now(UTC)
    report = IngestReport()
    for path in sorted(folder.glob("*.pdf")):
        try:
            document = await ingest_file(connection, path, moment)
        except IngestError as error:
            logger.warning("Материал %s не загружен: %s", path.name, error)
            report.failed[path.name] = str(error)
            continue
        item = document.item
        report.documents.append(
            {
                "id": item.id,
                "file": path.name,
                "title": item.title,
                "date": item.published_at.date().isoformat(),
                "fragments": len(item.fragments),
                "bank_view": len(item.key_facts),
                "topics": [topic.value for topic in item.classification.topics]
                if item.classification
                else [],
                "text": document.text_source,
            }
        )
    return report


async def load_documents(connection: Any) -> list[NewsItem]:
    records = await connection.fetch("SELECT item FROM analytics_documents ORDER BY published_at")
    return [NewsItem.model_validate_json(record["item"]) for record in records]
