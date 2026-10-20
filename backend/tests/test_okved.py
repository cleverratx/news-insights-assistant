import pytest

from app.contracts.profile import IndustryResolution, ValueChain
from app.profile.okved import (
    CLASSES,
    SECTIONS,
    SEGMENT_BY_CLASS,
    SEGMENT_BY_PREFIX,
    SEGMENTS,
    industry,
    normalize_code,
    resolve,
)


def test_every_class_has_section_and_segment() -> None:
    assert set(CLASSES) == set(SEGMENT_BY_CLASS)
    for class_code in CLASSES:
        assert any(int(class_code) in section.classes for section in SECTIONS), class_code
    assert set(SEGMENT_BY_CLASS.values()) | set(SEGMENT_BY_PREFIX.values()) <= set(SEGMENTS)


def test_sections_do_not_overlap() -> None:
    numbers = [number for section in SECTIONS for number in section.classes]
    assert len(numbers) == len(set(numbers))


def test_prefix_overrides_point_to_existing_classes() -> None:
    for prefix in SEGMENT_BY_PREFIX:
        assert prefix[:2] in CLASSES, prefix


def test_every_real_segment_feeds_search_and_topics() -> None:
    for segment in SEGMENTS.values():
        if segment.id != "other":
            assert segment.keywords, segment.id
            assert segment.topics, segment.id
            assert len(set(segment.keywords)) == len(segment.keywords), segment.id


@pytest.mark.parametrize(
    ("code", "segment", "section", "value_chain"),
    [
        ("46.38", "food", "G", ValueChain.WHOLESALE),
        ("32.50", "healthcare", "C", ValueChain.MANUFACTURING),
        ("62.01", "it_telecom", "J", ValueChain.SERVICES),
        ("46.90", "trade", "G", ValueChain.WHOLESALE),
        ("46.46", "healthcare", "G", ValueChain.WHOLESALE),
        ("46.4", "consumer_goods", "G", ValueChain.WHOLESALE),
        ("47.11", "food", "G", ValueChain.RETAIL),
        ("47.30", "oil_gas", "G", ValueChain.RETAIL),
        ("47.19", "retail", "G", ValueChain.RETAIL),
        ("68.20.2", "real_estate", "L", ValueChain.SERVICES),
        ("41.20", "construction", "F", ValueChain.CONSTRUCTION),
        ("56.10", "hospitality", "I", ValueChain.SERVICES),
        ("52.29", "logistics", "H", ValueChain.SERVICES),
        ("06.10", "oil_gas", "B", ValueChain.EXTRACTION),
        ("35.11", "utilities", "D", ValueChain.UTILITIES),
    ],
)
def test_codes_resolve_to_segments(
    code: str, segment: str, section: str, value_chain: ValueChain
) -> None:
    info = resolve(code)
    assert info.resolution is IndustryResolution.CODE
    assert info.segment.id == segment
    assert info.section is not None and info.section.letter == section
    assert info.value_chain is value_chain


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("46,38", "46.38"), (" 1.1 ", "01.1"), ("62.01.", "62.01"), ("abc", None), ("", None)],
)
def test_noisy_codes_normalized(raw: str, expected: str | None) -> None:
    assert normalize_code(raw) == expected


def test_missing_code_falls_back_to_name() -> None:
    info = resolve(None, "Торговля оптовая неспециализированная")
    assert info.resolution is IndustryResolution.NAME
    assert info.class_code == "46"
    assert info.segment.id == "trade"
    assert info.code is None


def test_nonexistent_class_keeps_code_and_uses_name() -> None:
    info = resolve("34.10", "Деятельность ресторанов и услуги по доставке продуктов питания")
    assert info.resolution is IndustryResolution.NAME
    assert info.code == "34.10"
    assert info.segment.id == "hospitality"


@pytest.mark.parametrize(("code", "name"), [("34.10", None), ("мусор", "Прочее"), (None, None)])
def test_unknown_industry_is_explicit(code: str | None, name: str | None) -> None:
    info = resolve(code, name)
    assert info.resolution is IndustryResolution.UNKNOWN
    assert info.segment.id == "other"
    assert info.section is None
    assert info.keywords == []


def test_industry_contract_for_profile() -> None:
    result = industry("62.01", "Разработка компьютерного программного обеспечения")
    assert result.okved_code == "62.01"
    assert result.section == "J"
    assert result.segment == "it_telecom"
    assert result.resolved_by is IndustryResolution.CODE
    assert "программное обеспечение" in result.keywords
    assert result.topics


def test_industry_name_falls_back_to_class_name() -> None:
    assert industry("10.11", "").okved_name == "Производство пищевых продуктов"
    assert industry(None, None).okved_name is None
