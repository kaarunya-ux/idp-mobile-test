"""The rules that decide whether a finished conversion is good (ML-07: the numbers come from settings).

Pure functions: the thresholds are passed in, nothing is read from a file or the clock (DOM-01, DOM-05).
"""

import re
from dataclasses import dataclass
from enum import StrEnum

_THOUSANDS_COMMA = re.compile(r"(?<=\d),(?=\d)")
_WORD = re.compile(r"[^\W_]+")


class Verdict(StrEnum):
    OK = "OK"
    REVIEW = "REVIEW"  # a file was written, but a person should look at it before trusting it


@dataclass(frozen=True, slots=True, kw_only=True)
class CheckThresholds:
    """Share of the source's words that must reach the output, and the OCR speckle limit."""

    min_word_share: float
    min_word_share_ocr: float
    ocr_min_word_chars: int


@dataclass(frozen=True, slots=True, kw_only=True)
class WordCheck:
    """How much of the source survived: the share kept and the words that did not."""

    share: float
    missing: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class Outcome:
    """The verdict of one conversion and why."""

    verdict: Verdict
    word_share: float
    reasons: tuple[str, ...]


def words_of(text: str) -> list[str]:
    """Words and numbers, lower-cased. ``$1,200.50`` and ``1200.50`` match; ``01`` and ``1`` are the same number."""
    tokens = _WORD.findall(_THOUSANDS_COMMA.sub("", text.lower()))
    return [(token.lstrip("0") or "0") if token.isdigit() else token for token in tokens]


def check_words(source_text: str, output_text: str, *, ocr: bool, thresholds: CheckThresholds) -> WordCheck:
    """Compare the words of source and output. A source read by OCR loses its one- and two-letter fragments first."""
    wanted = words_of(source_text)
    if ocr:  # OCR reads speckle and decoration as short "words"
        wanted = [word for word in wanted if len(word) >= thresholds.ocr_min_word_chars or word.isdigit()]
    if not wanted:
        return WordCheck(share=1.0, missing=())
    have = set(words_of(output_text))
    missing = tuple(dict.fromkeys(word for word in wanted if word not in have))
    kept = sum(1 for word in wanted if word in have)
    return WordCheck(share=kept / len(wanted), missing=missing)


def decide(
    check: WordCheck,
    *,
    ocr: bool,
    thresholds: CheckThresholds,
    tables_expected: int | None = None,
    tables_found: int | None = None,
) -> Outcome:
    """OK when enough words arrived and the table count matches; REVIEW otherwise, with the reasons."""
    floor = thresholds.min_word_share_ocr if ocr else thresholds.min_word_share
    reasons: list[str] = []
    if check.share < floor:
        reasons.append(f"{check.share:.1%} of the source's words reached the output; at least {floor:.0%} is needed")
    if tables_expected is not None and tables_found is not None and tables_expected != tables_found:
        reasons.append(
            f"{tables_found} tables in the output for {tables_expected} in the source "
            "(a table may have been cut in two, or a page holds several)"
        )
    return Outcome(verdict=Verdict.REVIEW if reasons else Verdict.OK, word_share=check.share, reasons=tuple(reasons))
