from pathlib import Path

import pytest

from app.data.__main__ import COMPANIES_FILE, LINKS_FILE, PAYMENTS_FILE, resolve_inputs
from app.data.loader import read_case
from app.data.reader import CaseFileError
from tests.test_data_loader import HEADER, company_line, write


def case_folder(root: Path, name: str, with_payments: bool) -> Path:
    folder = root / name
    folder.mkdir(parents=True)
    write(folder / COMPANIES_FILE, [HEADER, company_line()])
    write(folder / LINKS_FILE, ["SYNTHETIC_F;CITY_NAME;RC_NAME;SYNTHETIC_U", "F_1;;;U_1"])
    if with_payments:
        write(
            folder / PAYMENTS_FILE,
            [
                "BENEFICIARY;PAYER;PAY_AMT;PAY_CNT",
                "U_1;U_9;1000;2",
                "U_7;U_1;250;1",
                "U_1;U_1;10;1",
            ],
        )
    return folder


def test_payments_file_is_picked_up_by_default(tmp_path: Path) -> None:
    raw = case_folder(tmp_path, "raw", with_payments=True)
    inputs = resolve_inputs(tmp_path)
    assert inputs.companies == raw / COMPANIES_FILE
    assert inputs.links == raw / LINKS_FILE
    assert inputs.payments == raw / PAYMENTS_FILE


def test_payments_follow_explicit_links_folder(tmp_path: Path) -> None:
    case_folder(tmp_path, "raw", with_payments=False)
    other = case_folder(tmp_path, "other", with_payments=True)
    inputs = resolve_inputs(tmp_path, companies=other / COMPANIES_FILE, links=other / LINKS_FILE)
    assert inputs.payments == other / PAYMENTS_FILE


def test_no_payments_file_means_no_payments(tmp_path: Path) -> None:
    case_folder(tmp_path, "raw", with_payments=False)
    assert resolve_inputs(tmp_path).payments is None


def test_explicit_missing_payments_file_is_an_error(tmp_path: Path) -> None:
    case_folder(tmp_path, "raw", with_payments=False)
    with pytest.raises(CaseFileError, match="платежей не найден"):
        resolve_inputs(tmp_path, payments=tmp_path / "nope.csv")


def test_samples_ignore_raw_folder(tmp_path: Path) -> None:
    case_folder(tmp_path, "raw", with_payments=True)
    samples = case_folder(tmp_path, "samples", with_payments=False)
    inputs = resolve_inputs(tmp_path, samples=True)
    assert inputs.companies == samples / COMPANIES_FILE
    assert inputs.links == samples / LINKS_FILE
    assert inputs.payments is None


def test_default_run_loads_separate_payments(tmp_path: Path) -> None:
    case_folder(tmp_path, "raw", with_payments=True)
    inputs = resolve_inputs(tmp_path)
    data = read_case(inputs.companies, inputs.links, inputs.payments)
    report = data.report()
    assert set(data.payments) == {("U_9", "U_1"), ("U_1", "U_7")}
    assert report["tables"]["payments"]["rejected"] == 1
    assert report["warnings"] == []
