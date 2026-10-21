from app.api.feed import build_feed
from app.contracts.feed import Polarity
from app.rank.diversity import Entry, diversify, event_key, polarity
from tests.test_api_feed import material
from tests.test_news_classify import NOW
from tests.test_rank_rules import profile

RISK, CHANCE, CALM = Polarity.RISK, Polarity.OPPORTUNITY, Polarity.NEUTRAL


def entries(*specs: tuple[str, str, Polarity, float]) -> list[Entry]:
    return [Entry(id_, score, event, sign, id_) for id_, event, sign, score in specs]


def test_third_in_a_row_about_one_event_moves_down() -> None:
    result = diversify(
        entries(
            ("a1", "rate", CALM, 0.9),
            ("a2", "rate", CALM, 0.8),
            ("a3", "rate", CALM, 0.7),
            ("b", "oil", CALM, 0.6),
            ("a4", "rate", CALM, 0.5),
        )
    )
    assert result.order == ["a1", "a2", "b", "a3", "a4"]
    assert result.notes == ["«a3» сдвинут ниже: 2 материала подряд уже об этом событии"]


def test_single_event_feed_stays_as_is() -> None:
    result = diversify(entries(*((f"a{index}", "rate", CALM, 0.9) for index in range(4))))
    assert result.order == ["a0", "a1", "a2", "a3"]


def test_missing_opportunity_is_promoted_into_window() -> None:
    risks = [(f"r{index}", f"e{index}", RISK, 0.9 - index / 10) for index in range(6)]
    result = diversify(entries(*risks, ("o", "x", CHANCE, 0.4)))
    assert result.order.index("o") == 4
    assert "«o» поднят в первые 5: для баланса добавлена возможность" in result.notes


def test_weak_material_is_not_promoted_for_balance() -> None:
    risks = [(f"r{index}", f"e{index}", RISK, 0.9) for index in range(6)]
    result = diversify(entries(*risks, ("o", "x", CHANCE, 0.2)))
    assert result.order[-1] == "o"
    assert result.notes == []


def test_both_signs_fill_last_window_slots() -> None:
    calm = [(f"n{index}", f"e{index}", CALM, 0.9) for index in range(6)]
    result = diversify(entries(*calm, ("r", "y", RISK, 0.5), ("o", "x", CHANCE, 0.4)))
    assert result.order[3:5] == ["r", "o"]


def test_balanced_feed_is_untouched() -> None:
    specs = entries(("r", "a", RISK, 0.9), ("o", "b", CHANCE, 0.8), ("n", "c", CALM, 0.7))
    result = diversify(specs)
    assert result.order == ["r", "o", "n"]
    assert result.notes == []


def test_event_key_and_polarity_from_news() -> None:
    first = material("n1", "Роснефть", entities=["Роснефть"], sentiment="negative")
    second = material("n2", "Роснефть снова", cluster="story:2", entities=["РОСНЕФТЬ"])
    assert event_key(first) == event_key(second) == "industry:роснефть"
    assert event_key(material("n3", "Нефть", segments=["oil_gas"])) == "industry:oil_gas"
    plain = first.model_copy(update={"classification": None})
    assert event_key(plain) == "story:n1"
    assert polarity(first) is RISK
    assert polarity(second) is CALM
    assert polarity(material("n4", "Рост", sentiment="positive")) is CHANCE


async def test_feed_spreads_retellings_and_marks_polarity() -> None:
    retellings = [
        material(f"oil:{index}", f"Роснефть {index}", entities=["Роснефть"], segments=["oil_gas"])
        for index in range(3)
    ]
    region = material("region", "Область", regions=["Свердловская область"], sentiment="positive")
    result = await build_feed(profile(), [*retellings, region], None, now=NOW)
    assert [found.news_id for found in result.items][2] == "region"
    assert result.items[2].event.polarity is CHANCE
    assert result.items[0].event.polarity is CALM
    assert any("сдвинут ниже" in note for note in result.adjustments)
