"""Numbers are read as written; IDs, PINs and long card numbers stay text (IDP-03 ch. 3.4, PY-06)."""

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from conversion.conversion.domain.cell_values import (
    ColumnKind,
    WrittenNumber,
    has_header,
    number_format,
    parse_number,
    plan_columns,
)


@pytest.mark.parametrize(
    ("text", "mark", "value", "decimals", "grouped", "symbol", "percent"),
    [
        ("42", ".", "42", 0, False, "", False),
        ("1,250.50", ".", "1250.50", 2, True, "", False),
        ("1.250,50", ",", "1250.50", 2, True, "", False),
        ("₹950", ".", "950", 0, False, "₹", False),
        ("12.5%", ".", "0.125", 1, False, "", True),
        ("(300)", ".", "-300", 0, False, "", False),
        ("-7.25", ".", "-7.25", 2, False, "", False),
        ("0.5", ".", "0.5", 1, False, "", False),
    ],
)
def test_reads_the_number_and_how_it_was_written_when_text_is_a_number(
    text: str, mark: str, value: str, decimals: int, grouped: bool, symbol: str, percent: bool
) -> None:
    number = parse_number(text, mark)

    assert number == WrittenNumber(
        value=Decimal(value),
        decimals=decimals,
        grouped=grouped,
        symbol=symbol,
        symbol_after=False,
        percent=percent,
    )


@pytest.mark.parametrize(
    ("text", "why"),
    [
        ("0471", "leading zero: a PIN code"),
        ("007", "leading zero: an ID"),
        ("4111111111111111", "16 digits: a card number a spreadsheet would round"),
        ("12,34,567", "Indian-style grouping is not 3-digit groups"),
        ("1.2.3", "two decimal marks"),
        ("abc", "not a number"),
        ("(12", "unbalanced bracket"),
        ("", "empty"),
    ],
)
def test_keeps_it_as_text_when_it_is_not_a_plain_number(text: str, why: str) -> None:
    assert parse_number(text, ".") is None, why


@given(st.decimals(min_value=-(10**9), max_value=10**9, places=4, allow_nan=False, allow_infinity=False))
def test_reads_back_the_same_value_for_any_plainly_written_number(value: Decimal) -> None:
    text = format(value, "f")
    if text.lstrip("-").startswith("0") and "." not in text and text.lstrip("-") != "0":
        return  # a leading zero would make it an ID, by design

    number = parse_number(text, ".")

    assert number is not None
    assert number.value == value


@pytest.mark.parametrize(
    ("number", "grouped_column", "expected"),
    [
        (WrittenNumber(Decimal(10), 0, False, "", False, False), False, "General"),
        (WrittenNumber(Decimal("1250.5"), 2, True, "", False, False), True, "#,##0.00"),
        (WrittenNumber(Decimal(950), 0, False, "", False, False), True, "General"),  # under 1,000 in a grouped column
        (WrittenNumber(Decimal(1200), 0, False, "", False, False), True, "#,##0"),
        (WrittenNumber(Decimal(950), 0, False, "₹", False, False), False, '"₹"0'),
        (WrittenNumber(Decimal(950), 2, False, "€", True, False), False, '0.00"€"'),
        (WrittenNumber(Decimal("0.125"), 1, False, "", False, True), False, "0.0%"),
    ],
)
def test_picks_the_format_that_shows_the_number_as_written(
    number: WrittenNumber, grouped_column: bool, expected: str
) -> None:
    assert number_format(number, column_grouped=grouped_column) == expected


def test_types_a_column_as_numbers_when_nearly_all_values_parse() -> None:
    body = [["Paper", "10"], ["Pens", "24"], ["Card", "1"]]

    names, quantities = plan_columns(body, 2)

    assert names.kind is ColumnKind.TEXT
    assert quantities.kind is ColumnKind.NUMBER
    assert [n.value for n in quantities.numbers if n] == [10, 24, 1]


def test_keeps_numbers_as_text_when_the_file_writes_decimals_with_a_comma() -> None:
    body = [["1.200,50", "3"], ["950,00", "4"]]

    first, second = plan_columns(body, 2)

    assert first.kind is ColumnKind.NUMBER_AS_TEXT
    assert second.kind is ColumnKind.NUMBER_AS_TEXT  # "." means thousands in this file, so nothing is safe to parse


def test_keeps_a_mostly_text_column_as_text_when_under_95_percent_parse() -> None:
    body = [["10"], ["n/a"], ["12"], ["unknown"]]

    (column,) = plan_columns(body, 1)

    assert column.kind is ColumnKind.TEXT


@pytest.mark.parametrize(
    ("rows", "expected"),
    [
        ([["item", "qty"], ["Paper", "10"]], True),  # words over numbers
        ([["1958", "1959"], ["340", "360"]], False),  # all numbers: no way to tell, so no header
        # Known limit of the ported rule: a first row with words is taken as a header even when it is data.
        # The data can't tell the two apart; the cost is header styling on one row, never lost data.
        ([["Paper", "10"], ["Pens", "24"]], True),
        ([["name", "name"], ["a", "b"]], False),  # repeated names aren't headers
        ([], False),
    ],
)
def test_detects_a_header_row_when_the_data_shows_one(rows: list[list[str]], expected: bool) -> None:
    assert has_header(rows) is expected
