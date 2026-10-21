import struct
import zlib
from datetime import UTC, date, datetime
from pathlib import Path

import asyncpg
import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject
from pypdf.generic import NumberObject as Number

from app.analytics import ingest
from app.analytics.ingest import (
    IngestError,
    Page,
    authors,
    bank_view,
    build,
    document_date,
    headings,
    ingest_dir,
    load_documents,
    page_images,
    paragraphs,
    png,
    tesseract,
    themes,
)
from app.contracts.news import FactStatus, Fragment, MaterialKind, Topic
from tests.test_data_loader import database

__all__ = ["database"]

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
PAGE_ONE = """Центр макроэкономического анализа

Ирина Петрова,
IPetrova@alfabank.ru
15 сентября 2026 г. Олег Смирнов,

OSmirnov@alfabank.ru
Ключевая ставка ЦБ РФ Анна Кузнецова,
Ставка сохранена на 14,0%, сигнал стал жёстче

ЦБ РФ сохранил ставку на уровне 14,0%: совет директоров взял паузу и оставил
ставку без изменений, а пресс-
релиз содержит корректировки в сторону ужесточения.

Рынок труда остаётся перегретым: безработица держится у минимумов, зарплаты
растут быстрее производительности, что поддерживает инфляцию и потребление.

40
30
2024 2025 2026

Мы ожидаем сохранения ставки 14,0% до конца года. По нашему мнению, риски
для инфляции смещены вверх.

Альфа-Банк, 2026 г. Все права защищены. Генеральная лицензия ЦБ РФ № 1326.

1
"""


def test_paragraphs_join_lines_and_drop_noise() -> None:
    found = paragraphs(PAGE_ONE)
    assert found[0].startswith("Ставка сохранена") is False
    rate = next(text for text in found if text.startswith("ЦБ РФ сохранил"))
    assert "пресс-релиз содержит" in rate
    assert not any("Все права защищены" in text for text in found)
    assert not any(text.startswith(("40", "2024")) for text in found)
    assert not any("@" in text for text in found)


def test_headings_dates_and_authors() -> None:
    pages = [Page(1, PAGE_ONE, "tesseract")]
    assert headings(pages) == (
        "Ключевая ставка ЦБ РФ",
        "Ставка сохранена на 14,0%, сигнал стал жёстче",
    )
    assert document_date(pages, "x.pdf") == date(2026, 9, 15)
    assert authors(pages) == ["Ирина Петрова", "Олег Смирнов", "Анна Кузнецова"]
    empty = [Page(1, "ОБЗОР РЫНКА ЗОЛОТА\nАВГУСТ 2026", "tesseract")]
    assert document_date(empty, "x.pdf") == date(2026, 8, 31)
    assert headings(empty) == ("ОБЗОР РЫНКА ЗОЛОТА", None)
    blank = [Page(1, "без даты", "tesseract")]
    assert document_date(blank, "Brief_14092026.pdf") == date(2026, 9, 14)
    assert document_date(blank, "Steel_15Apr2021.pdf") == date(2021, 4, 15)
    assert document_date(blank, "x.pdf") is None
    with pytest.raises(IngestError):
        headings([Page(1, "гоу If aad\n12", "tesseract")])


def test_bank_view_and_themes() -> None:
    fragment = Fragment(
        id="s1-3",
        text="Рост замедлился. Мы ожидаем сохранения ставки до конца года. "
        "По нашему мнению, риски смещены вверх.",
    )
    facts = bank_view([fragment])
    assert [(fact.status, fact.fragment_id) for fact in facts] == [
        (FactStatus.FORECAST, "s1-3"),
        (FactStatus.ASSUMPTION, "s1-3"),
    ]
    assert facts[0].text == "Мы ожидаем сохранения ставки до конца года."
    assert themes("ключевая ставка ставка ОФЗ облигации облигаций")[0] is Topic.KEY_RATE
    assert themes("ни о чём") == [Topic.MACRO]


def test_build_document() -> None:
    page_two = "Потребление на пике: спрос населения начинает охлаждаться после сильного 1П26."
    document = build(
        [Page(1, PAGE_ONE, "tesseract"), Page(2, page_two, "tesseract")],
        "rate.pdf",
        "a" * 64,
        NOW,
    )
    item = document.item
    assert item.id == "bank:" + "a" * 16
    assert item.kind is MaterialKind.BANK_ANALYTICS
    assert item.title == "Ключевая ставка ЦБ РФ: Ставка сохранена на 14,0%, сигнал стал жёстче"
    assert item.published_at == datetime(2026, 9, 15, 15, 0, tzinfo=UTC)
    assert item.fragments[0].id == "s1-1"
    assert item.fragments[-1].locator == "стр. 2, абзац 1"
    assert item.key_facts[0].text.startswith("Мы ожидаем")
    assert item.classification is not None
    assert Topic.KEY_RATE in item.classification.topics
    assert document.text_source == "ocr"
    with pytest.raises(IngestError):
        build([Page(1, "Заголовок материала\nкоротко", "pdf_text")], "x.pdf", "b" * 64, NOW)


def test_png_header() -> None:
    image = png(2, 1, 3, bytes(6))
    assert image.startswith(b"\x89PNG\r\n\x1a\n")
    width, height = struct.unpack(">II", image[16:24])
    assert (width, height) == (2, 1)


def test_missing_ocr_is_clear_error() -> None:
    with pytest.raises(IngestError, match="OCR не найден"):
        tesseract(b"x", "no-such-ocr-binary", "rus")


def scanned_pdf(path: Path, pages: int = 2) -> None:
    """PDF из страниц-картинок без текстового слоя, как материалы кейса."""
    writer = PdfWriter()
    for _ in range(pages):
        page = writer.add_blank_page(100, 100)
        image = DecodedStreamObject()
        image.set_data(bytes(3 * 4 * 4))
        image.update(
            {
                NameObject("/Type"): NameObject("/XObject"),
                NameObject("/Subtype"): NameObject("/Image"),
                NameObject("/Width"): Number(4),
                NameObject("/Height"): Number(4),
                NameObject("/ColorSpace"): NameObject("/DeviceRGB"),
                NameObject("/BitsPerComponent"): Number(8),
            }
        )
        reference = writer._add_object(image)
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/XObject"): DictionaryObject({NameObject("/Im1"): reference})}
        )
        page[NameObject("/Contents")] = writer._add_object(DecodedStreamObject())
        page[NameObject("/MediaBox")] = ArrayObject(
            [Number(0), Number(0), Number(100), Number(100)]
        )
    with path.open("wb") as handle:
        writer.write(handle)


def test_page_images_from_scan(tmp_path: Path) -> None:
    path = tmp_path / "scan.pdf"
    scanned_pdf(path, 1)
    [image] = page_images(PdfReader(path), 0)
    assert image.startswith(b"\x89PNG")
    assert zlib.decompress(image[41:-12])[:1] == b"\x00"


async def test_ingest_dir_with_ocr_cache(
    database: asyncpg.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scanned_pdf(tmp_path / "rate.pdf")
    (tmp_path / "broken.pdf").write_bytes(b"not a pdf")
    calls: list[bytes] = []

    def fake_ocr(image: bytes, command: str, languages: str) -> str:
        calls.append(image)
        return (
            PAGE_ONE if len(calls) == 1 else "Вторая страница обзора с длинным абзацем про спрос."
        )

    monkeypatch.setattr(ingest, "tesseract", fake_ocr)
    report = await ingest_dir(database, tmp_path, NOW)
    assert list(report.failed) == ["broken.pdf"]
    [summary] = report.documents
    assert (
        summary["title"] == "Ключевая ставка ЦБ РФ: Ставка сохранена на 14,0%, сигнал стал жёстче"
    )
    assert summary["date"] == "2026-09-15"
    assert summary["text"] == "ocr"
    assert len(calls) == 2

    again = await ingest_dir(database, tmp_path, NOW)
    assert len(again.documents) == 1
    assert len(calls) == 2
    [item] = await load_documents(database)
    assert item.kind is MaterialKind.BANK_ANALYTICS
    stored = await database.fetchrow("SELECT authors, topics, pages FROM analytics_documents")
    assert stored["authors"] == ["Ирина Петрова", "Олег Смирнов", "Анна Кузнецова"]
    assert stored["pages"] == 2
    assert "key_rate" in stored["topics"]
