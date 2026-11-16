"""Sheet names follow Excel's rules: no forbidden characters, at most 31 characters, unique in the workbook."""

import pytest

from conversion.conversion.infrastructure.converters.spreadsheets import sheet_title


@pytest.mark.parametrize(
    ("name", "expected"),
    [("Sales", "Sales"), ("Q1/Q2: [draft]?", "Q1 Q2   draft"), ("   ", "Sheet"), ("x" * 40, "x" * 31)],
)
def test_makes_a_name_excel_accepts_when_given_any_text(name: str, expected: str) -> None:
    assert sheet_title(name, set()) == expected


def test_numbers_repeats_case_insensitively_and_stays_within_31_characters() -> None:
    taken: set[str] = set()

    names = [sheet_title(name, taken) for name in ["Sales", "SALES", "sales", "y" * 31, "y" * 31]]

    assert names == ["Sales", "SALES (2)", "sales (3)", "y" * 31, "y" * 27 + " (2)"]
    assert all(len(name) <= 31 for name in names)
