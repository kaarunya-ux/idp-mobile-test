"""The normalizers a template can list for a field, applied in order (IDP-03 ch. 2.5, ``normalize``).

OCR confuses a few letters and digits (O and 0, I and 1, S and 5, B and 8). ``OCR_CONFUSABLES_NUMERIC`` fixes them in
a value that must be all digits; ``OCR_CONFUSABLES_ALNUM`` fixes them position by position, using the rule's pattern
to know where a letter or a digit belongs. The tables hold exactly the pairs IDP-03 names; the prototype also mapped
D, Q, L, Z and G, which is flagged for confirmation against a benchmark before they are added (ML-12).
"""

import re
from collections.abc import Callable, Sequence
from enum import StrEnum

from extraction.rule.exceptions import UnsupportedDefinitionError

_TO_DIGIT = str.maketrans({"O": "0", "I": "1", "l": "1", "S": "5", "B": "8"})
_TO_LETTER = str.maketrans({"0": "O", "1": "I", "5": "S", "8": "B"})
_SPACES = re.compile(r"\s+")
_NON_DIGITS = re.compile(r"[^0-9]")  # ASCII digits only: \d would keep digits of other scripts
_ATOM = re.compile(r"(?P<atom>\[(?P<members>[^\]]+)\]|\\d|[A-Za-z0-9/.\- ])(?:\{(?P<count>\d+)\})?")
_LETTER_MEMBERS = re.compile(r"(?:[A-Za-z](?:-[A-Za-z])?)+")
_DIGIT_MEMBERS = re.compile(r"(?:[0-9](?:-[0-9])?)+")


class Normalizer(StrEnum):
    """The names a template definition uses for each operation."""

    UPPER = "UPPER"
    LOWER = "LOWER"
    STRIP_SPACES = "STRIP_SPACES"
    COLLAPSE_SPACES = "COLLAPSE_SPACES"
    DIGITS_ONLY = "DIGITS_ONLY"
    OCR_CONFUSABLES_NUMERIC = "OCR_CONFUSABLES_NUMERIC"
    OCR_CONFUSABLES_ALNUM = "OCR_CONFUSABLES_ALNUM"


class _Position(StrEnum):
    LETTER = "LETTER"
    DIGIT = "DIGIT"
    ANY = "ANY"


def normalize_value(value: str, operations: Sequence[Normalizer], *, pattern: str | None = None) -> str:
    """Apply ``operations`` in order. ``pattern`` is the rule's regular expression, needed by OCR_CONFUSABLES_ALNUM."""
    result = value
    for operation in operations:
        result = _apply(result, operation, pattern)
    return result


def _apply(value: str, operation: Normalizer, pattern: str | None) -> str:
    if operation is Normalizer.OCR_CONFUSABLES_ALNUM:
        if pattern is None:
            message = "OCR_CONFUSABLES_ALNUM needs the rule's pattern to know where letters and digits belong."
            raise UnsupportedDefinitionError(message)
        return _fix_by_position(value, _read_positions(pattern))
    return _SIMPLE_OPERATIONS[operation](value)


def _strip_spaces(value: str) -> str:
    return _SPACES.sub("", value)


def _collapse_spaces(value: str) -> str:
    return _SPACES.sub(" ", value).strip()


def _keep_digits(value: str) -> str:
    return _NON_DIGITS.sub("", value)


def _fix_numeric(value: str) -> str:
    return value.translate(_TO_DIGIT)


_SIMPLE_OPERATIONS: dict[Normalizer, Callable[[str], str]] = {
    Normalizer.UPPER: str.upper,
    Normalizer.LOWER: str.lower,
    Normalizer.STRIP_SPACES: _strip_spaces,
    Normalizer.COLLAPSE_SPACES: _collapse_spaces,
    Normalizer.DIGITS_ONLY: _keep_digits,
    Normalizer.OCR_CONFUSABLES_NUMERIC: _fix_numeric,
}


def _read_positions(pattern: str) -> tuple[_Position, ...]:
    """Turn a fixed-length pattern such as ``^[A-Z]{5}[0-9]{4}[A-Z]$`` into one expected kind per character.

    Only fixed-length patterns built from character classes, ``\\d``, literals and ``{n}`` counts are understood;
    anything else raises ``UnsupportedDefinitionError``.
    """
    body = pattern.removeprefix("^").removesuffix("$")
    positions: list[_Position] = []
    cursor = 0
    while cursor < len(body):
        match = _ATOM.match(body, cursor)
        if match is None:
            message = f"The pattern {pattern!r} is not a fixed-length pattern of classes and counts."
            raise UnsupportedDefinitionError(message)
        positions.extend([_kind_of(match)] * int(match.group("count") or 1))
        cursor = match.end()
    return tuple(positions)


def _kind_of(match: re.Match[str]) -> _Position:
    members = match.group("members")
    if members is not None and _LETTER_MEMBERS.fullmatch(members):
        return _Position.LETTER
    if match.group("atom") == "\\d" or (members is not None and _DIGIT_MEMBERS.fullmatch(members)):
        return _Position.DIGIT
    return _Position.ANY


def _fix_by_position(value: str, positions: tuple[_Position, ...]) -> str:
    """Swap look-alikes where the pattern says a digit or a letter belongs; a length mismatch changes nothing."""
    if len(value) != len(positions):
        return value
    fixed: list[str] = []
    for character, position in zip(value, positions, strict=True):
        if position is _Position.DIGIT:
            fixed.append(character.translate(_TO_DIGIT))
        elif position is _Position.LETTER:
            fixed.append(character.translate(_TO_LETTER))
        else:
            fixed.append(character)
    return "".join(fixed)
