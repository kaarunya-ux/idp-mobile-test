"""Reading values out of CSV text: numbers as they were written, IDs and codes kept as text (IDP-03 ch. 3.4).

Pure rules, no I/O. Ported from ``services/conversions/python_scripts/csv_convert.py`` (Sumukh, Karunya), the
path without the AI model. Numbers are ``Decimal``, not ``float``, so money and measurements stay exact (PY-06).
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

NUMERIC_SHARE = 0.95  # share of a column's filled cells that must be numbers before the column holds numbers
THOUSANDS_GROUP = 3
EXCEL_SIGNIFICANT_DIGITS = 15  # Excel keeps 15 significant digits and rounds the rest
GROUPING_STARTS_AT = Decimal(1000)  # from here a grouped column shows separators
_PERCENT = Decimal(100)
_NUMBER = re.compile(  # \u2212 is the typographic minus sign used in typeset documents
    r"^(?P<neg>[-\u2212(])?\s*(?P<cur1>[$€£₹¥])?\s*(?P<num>\d[\d,.' ]*)\s*(?P<cur2>[$€£₹¥])?\s*(?P<pct>%)?\)?$"
)


@dataclass(frozen=True, slots=True)
class WrittenNumber:
    """A number read from text, plus how it was written, so a spreadsheet can show it the same way."""

    value: Decimal
    decimals: int
    grouped: bool  # written with a thousands separator
    symbol: str  # currency symbol, or ""
    symbol_after: bool
    percent: bool


def parse_number(text: str, decimal_mark: str) -> WrittenNumber | None:
    """Read ``text`` as a number whose decimal point is ``decimal_mark`` ("." or ","); ``None`` if it isn't one.

    A whole number with a leading zero ("007", "0471") is an ID, a PIN or a phone number, never a number; so is
    anything longer than 15 significant digits, which a spreadsheet can't store without rounding.
    """
    parts = _number_parts(text.strip(), decimal_mark)
    if parts is None:
        return None
    match, digits, fraction, grouped = parts
    value = Decimal(f"{digits}.{fraction}" if fraction else digits)
    percent = bool(match.group("pct"))
    return WrittenNumber(
        value=(-value if match.group("neg") else value) / (_PERCENT if percent else 1),
        decimals=len(fraction),
        grouped=grouped,
        symbol=match.group("cur1") or match.group("cur2") or "",
        symbol_after=bool(match.group("cur2")),
        percent=percent,
    )


def _number_parts(text: str, decimal_mark: str) -> tuple[re.Match[str], str, str, bool] | None:
    """(match, digits, fraction, grouped) when ``text`` is a well-formed number that isn't an ID."""
    match = _NUMBER.match(text)
    if match is None or text.startswith("(") != text.endswith(")"):
        return None
    body = match.group("num").replace(" ", "").replace("'", "")
    thousands = "," if decimal_mark == "." else "."
    whole, _, fraction = body.partition(decimal_mark)
    digits = whole.replace(thousands, "")
    malformed = (
        body.count(decimal_mark) > 1
        or not _valid_grouping(whole, thousands)
        or not digits.isdigit()
        or (bool(fraction) and not fraction.isdigit())
    )
    looks_like_an_id = (len(digits) > 1 and digits.startswith("0") and not fraction) or (
        len(digits.lstrip("0")) + len(fraction) > EXCEL_SIGNIFICANT_DIGITS  # a card or account number
    )
    if malformed or looks_like_an_id:
        return None
    return match, digits, fraction, thousands in whole


def _valid_grouping(whole: str, thousands: str) -> bool:
    if thousands not in whole:
        return True
    groups = whole.split(thousands)
    return 1 <= len(groups[0]) <= THOUSANDS_GROUP and all(len(group) == THOUSANDS_GROUP for group in groups[1:])


class ColumnKind(StrEnum):
    NUMBER = "NUMBER"  # stored as numbers, with a format that shows them as written
    NUMBER_AS_TEXT = "NUMBER_AS_TEXT"  # numbers with a decimal comma: a number format can't show that, so kept as text
    TEXT = "TEXT"  # text, IDs, codes and dates: kept exactly as written


@dataclass(frozen=True, slots=True)
class ColumnPlan:
    kind: ColumnKind
    numbers: tuple[WrittenNumber | None, ...]  # one per row when kind is NUMBER
    grouped: bool  # at least one value used a thousands separator


def plan_columns(body: Sequence[Sequence[str]], column_count: int) -> list[ColumnPlan]:
    """Decide, per column, whether it holds numbers. Rows are padded to ``column_count``."""
    readings = [_read_column([row[j] for row in body]) for j in range(column_count)]
    numeric = [share >= NUMERIC_SHARE for share, _, _ in readings]
    # A file that writes decimals with a comma uses "." for thousands ("1.200" is twelve hundred), so all of its
    # number columns are kept exactly as written.
    comma_file = any(
        is_number and mark == "," and any("," in row[j] for row in body)
        for j, (is_number, (_, mark, _)) in enumerate(zip(numeric, readings, strict=True))
    )
    plans: list[ColumnPlan] = []
    for is_number, (_, mark, numbers) in zip(numeric, readings, strict=True):
        if not is_number:
            plans.append(ColumnPlan(kind=ColumnKind.TEXT, numbers=(), grouped=False))
        elif mark == "," or comma_file:
            plans.append(ColumnPlan(kind=ColumnKind.NUMBER_AS_TEXT, numbers=(), grouped=False))
        else:
            grouped = any(number.grouped for number in numbers if number)
            plans.append(ColumnPlan(kind=ColumnKind.NUMBER, numbers=numbers, grouped=grouped))
    return plans


def _read_column(values: Sequence[str]) -> tuple[float, str, tuple[WrittenNumber | None, ...]]:
    """(share of filled cells that parse, decimal mark that parses the most, parsed values)."""
    filled = sum(1 for value in values if value)
    best: tuple[float, str, tuple[WrittenNumber | None, ...]] = (0.0, ".", (None,) * len(values))
    for mark in (".", ","):
        parsed = tuple(parse_number(value, mark) if value else None for value in values)
        share = sum(1 for number in parsed if number) / filled if filled else 0.0
        if share > best[0]:
            best = (share, mark, parsed)
    return best


def number_format(number: WrittenNumber, *, column_grouped: bool) -> str:
    """The spreadsheet format that shows ``number`` as it was written ("1,200.50", "₹950", "12.5%").

    The thousands separator follows the column, so 950 and 1,200 sit side by side in the same style.
    """
    grouped = number.grouped or (column_grouped and abs(number.value) >= GROUPING_STARTS_AT)
    core = ("#,##0" if grouped else "0") + ("." + "0" * number.decimals if number.decimals else "")
    if number.percent:
        return core + "%"
    if number.symbol:
        return f'{core}"{number.symbol}"' if number.symbol_after else f'"{number.symbol}"{core}'
    return core if grouped or number.decimals else "General"


def has_header(rows: Sequence[Sequence[str]]) -> bool:
    """Whether the first row is a header.

    It is when its cells hold words over a column of numbers. Failing that, it is when every cell is filled and
    distinct and at least one cell isn't a number.
    """
    if not rows:
        return False
    first = rows[0]
    if len(rows) > 1:
        for j, cell in enumerate(first):
            column_is_numeric = _read_column([row[j] for row in rows[1:]])[0] >= NUMERIC_SHARE
            if cell and column_is_numeric and not (parse_number(cell, ".") or parse_number(cell, ",")):
                return True
    return all(first) and len(set(first)) == len(first) and any(not parse_number(cell, ".") for cell in first)
