import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pandas as pd
import pytest
import yaml

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
BIG = "20000000000"


@pytest.fixture
def picker(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.syspath_prepend(str(SCRIPTS))
    monkeypatch.delitem(sys.modules, "pick_demo_companies", raising=False)
    import pick_demo_companies

    return pick_demo_companies


def company(company_id: str, code: str, **changes: Any) -> dict[str, Any]:
    row = {
        "SYNTHETIC_U": company_id,
        "MAINOKVED_CODE": code,
        "MAINOKVED_NAME": f"отрасль {code}",
        "REGION": "Москва",
        "REVENUE_COMPANY": BIG,
        "FTS_FLAG": "1",
        "FTS_COUNTRY_NAME": "КИТАЙ",
        "CL_PRODUCT_LOA_MARK": "1",
        "CL_PRODUCT_LEA_MARK": "0",
        "GROUP_ID": f"group_{company_id}",
        "CL_TYPE": "CLIENT",
    }
    row.update(changes)
    return row


def payments(company_id: str, count: int, amount: int = 100) -> list[dict[str, Any]]:
    return [
        {
            "BENEFICIARY": company_id if number % 2 else f"P_{company_id}_{number}",
            "PAYER": f"P_{company_id}_{number}" if number % 2 else company_id,
            "PAY_AMT": amount * (number + 1),
            "PAY_CNT": 1,
        }
        for number in range(count)
    ]


def frames(rows: list[dict[str, Any]], partners: dict[str, int]) -> tuple[pd.DataFrame, ...]:
    companies = pd.DataFrame(rows)
    # Каждой компании — сосед по группе, чтобы группа была не из одной компании.
    mates = [
        company(
            f"{row['SYNTHETIC_U']}_mate", "68.20", GROUP_ID=row["GROUP_ID"], REVENUE_COMPANY="1"
        )
        for row in rows
        if isinstance(row.get("GROUP_ID"), str) and row["GROUP_ID"]
    ]
    companies = pd.concat([companies, pd.DataFrame(mates)], ignore_index=True)
    paid = [item for company_id, count in partners.items() for item in payments(company_id, count)]
    persons = pd.DataFrame(columns=["SYNTHETIC_F", "SYNTHETIC_U"])
    return companies, persons, pd.DataFrame(paid)


def test_one_company_per_role_among_fit_ones(picker: ModuleType) -> None:
    rows = [
        company("U_TRADE", "46.75"),
        company("U_TRADE_2", "46.71"),
        company("U_PLANT", "20.13", CL_PRODUCT_LOA_MARK="0", CL_PRODUCT_LEA_MARK="1"),
        company("U_SHOP", "47.30"),
        company("U_RICH_NO_GROUP", "46.38", GROUP_ID=""),
        company("U_NO_FTS", "24.10", FTS_FLAG="0"),
        company("U_NO_DEBT", "25.11", CL_PRODUCT_LOA_MARK="0"),
    ]
    partners = {row["SYNTHETIC_U"]: 8 for row in rows}
    partners["U_RICH_NO_GROUP"] = 40
    picked, segment_only = picker.build_cards(*frames(rows, partners), 3, 15e9)

    assert segment_only
    assert [item["id"] for item in picked] == ["U_TRADE", "U_PLANT", "U_SHOP"]
    assert [picker.archetype_title(item) for item in picked] == [
        "импортёр",
        "производство",
        "услуги или ритейл",
    ]
    assert all(not item["blockers"] for item in picked)


def test_blockers_name_what_is_missing(picker: ModuleType) -> None:
    rows = [company("U_POOR", "10.20", FTS_FLAG="0", CL_PRODUCT_LOA_MARK="0", GROUP_ID=None)]
    picked, _ = picker.build_cards(*frames(rows, {"U_POOR": 2}), 1, 15e9)

    assert picked[0]["blockers"] == [
        "нет ВЭД (FTS_FLAG)",
        "нет кредита или лизинга",
        "нет группы компаний (GROUP_ID)",
        "контрагентов меньше 5: 2",
    ]
    assert "Не проходит отбор" in picker.render_card(picked[0])


def test_self_payments_are_not_partners(picker: ModuleType) -> None:
    rows = [company("U_SELF", "46.75")]
    _, persons, paid = frames(rows, {"U_SELF": 4})
    self_payment = {"BENEFICIARY": "U_SELF", "PAYER": "U_SELF", "PAY_AMT": 1, "PAY_CNT": 1}
    paid = pd.concat([paid, pd.DataFrame([self_payment])], ignore_index=True)
    picked, _ = picker.build_cards(frames(rows, {})[0], persons, paid, 1, 15e9)
    assert picked[0]["partners"] == 4


def test_empty_role_is_filled_by_other_fit_company(picker: ModuleType) -> None:
    rows = [company("U_TRADE", "46.75"), company("U_BUILD", "41.20"), company("U_FARM", "01.11")]
    picked, _ = picker.build_cards(*frames(rows, {row["SYNTHETIC_U"]: 6 for row in rows}), 3, 15e9)
    assert picked[0]["id"] == "U_TRADE"
    assert {item["id"] for item in picked} == {"U_TRADE", "U_BUILD", "U_FARM"}
    assert picker.archetype_title(picked[1]) == "другая отрасль"


def test_legend_template_fits_legend_format(picker: ModuleType) -> None:
    # Чётные контрагенты получают деньги от компании, нечётные — платят ей.
    rows = [company("U_TRADE", "46.75"), company("P_U_TRADE_4", "49.41", GROUP_ID=None)]
    picked, _ = picker.build_cards(*frames(rows, {"U_TRADE": 6}), 1, 15e9)
    template = yaml.safe_load(picker.render_legend(picked))

    entry = template["companies"][0]
    assert entry["synthetic_u"] == "U_TRADE"
    assert set(entry) >= {"name", "description", "brands", "partners"}
    roles = {partner["synthetic_u"]: partner for partner in entry["partners"]}
    assert roles["P_U_TRADE_4"]["role"] == "перевозчик"
    assert "industry" not in roles["P_U_TRADE_4"], "отрасль есть в выгрузке — правы данные"
    assert roles["P_U_TRADE_5"]["role"] == "покупатель"
    assert roles["P_U_TRADE_2"]["role"] == "поставщик"
    assert roles["P_U_TRADE_2"]["industry"] == ""
    assert {partner["role"] for partner in entry["partners"]} <= {
        "покупатель",
        "поставщик",
        "перевозчик",
    }


def test_without_fit_companies_missing_roles_go_first(picker: ModuleType) -> None:
    rows = [
        # Ферма по баллу сильнее, но её роли нет в списке: недостающие роли важнее.
        company("U_FARM", "01.11", CL_PRODUCT_LOA_MARK="0", CL_PRODUCT_GAR_MARK="1"),
        company("U_TRADE", "46.75", FTS_FLAG="0"),
        company("U_PLANT", "20.13", FTS_FLAG="0"),
    ]
    picked, _ = picker.build_cards(*frames(rows, {row["SYNTHETIC_U"]: 6 for row in rows}), 2, 15e9)
    assert [item["id"] for item in picked] == ["U_TRADE", "U_PLANT"]
    assert all(item["blockers"] for item in picked)
