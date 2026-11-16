"""Where on the page a value the AI read is printed: the generic read's fields get a box from the OCR words.

The AI reads the values; OCR knows where each word is. A value is placed on the run of words that spells it (letters
and digits only, case ignored). When it is printed more than once (two dates), the run nearest its printed label is
taken; a value that can't be told apart from another place, or isn't found, gets no box. Never a guess.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass, replace

from extraction.quick.domain.models import ExtractedField
from extraction.quick.domain.placed import PlacedBox
from idp_ocr_engines.model import Box, OcrPage, OcrWord

MAX_RUN_WORDS = 40  # a value spans at most this many words (a long address)
_SHORT = 3  # a value this short ("F", "33") is placed only when its label shows which one it is


@dataclass(frozen=True, slots=True, kw_only=True)
class _Match:
    page: int
    words: tuple[OcrWord, ...]


def place_fields(fields: Sequence[ExtractedField], pages: Sequence[OcrPage]) -> list[ExtractedField]:
    """The fields, each text value with its box where it is found; tables and empty values stay without one."""
    return [
        replace(field, box=locate(field.value, field.label, pages)) if isinstance(field.value, str) else field
        for field in fields
    ]


def locate(value: str, label: str, pages: Sequence[OcrPage]) -> PlacedBox | None:
    """The box of ``value`` on ``pages``, or None when it isn't found or can't be told apart from another place."""
    target = _key(value)
    if not target:
        return None
    matches = [match for number, page in enumerate(pages, start=1) for match in _matches(target, number, page)]
    if not matches and len(target) >= _SHORT:  # OCR joined it to its label ("Email:a@b.com"): that one word
        matches = [
            _Match(page=number, words=(word,))
            for number, page in enumerate(pages, start=1)
            for word in page.words
            if target in _key(word.text)
        ]
    if not matches:
        return None
    chosen = _choose(matches, label, pages, short=len(target) < _SHORT)
    return PlacedBox(page=chosen.page, box=union_box(chosen.words)) if chosen else None


def _matches(target: str, page_number: int, page: OcrPage) -> list[_Match]:
    """Every shortest run of words whose letters and digits are exactly ``target``."""
    words = page.words
    found: list[_Match] = []
    for start in range(len(words)):
        text = ""
        for end in range(start, min(start + MAX_RUN_WORDS, len(words))):
            text += _key(words[end].text)
            if not target.startswith(text):
                break
            if text == target:
                found.append(_Match(page=page_number, words=words[start : end + 1]))
                break
    return found


def _choose(matches: list[_Match], label: str, pages: Sequence[OcrPage], *, short: bool) -> _Match | None:
    """A long value printed once: that place. Otherwise the place nearest its printed label, and None when the label
    isn't found or no place sits after it (a short value like "F" could be anywhere)."""
    if len(matches) == 1 and not short:
        return matches[0]
    wanted = _key(label)
    labels = (
        [run for number, page in enumerate(pages, start=1) for run in _matches(wanted, number, page)] if wanted else []
    )
    scored = [(min((_distance(run, match) for run in labels), default=math.inf), match) for match in matches]
    nearest = min(scored, key=lambda pair: pair[0])
    return nearest[1] if math.isfinite(nearest[0]) else None


def _distance(label: _Match, value: _Match) -> float:
    """How far a value sits from a label: labels come before their value (left of it or above), on the same page."""
    if label.page != value.page:
        return math.inf
    a, b = union_box(label.words), union_box(value.words)
    dx, dy = b.x - (a.x + a.width), b.y - a.y
    if dy < -a.height or (dx < -b.width and dy < a.height / 2):
        return math.inf  # above the label, or to its left on the same line: not this label's value
    return math.hypot(max(dx, 0.0), max(dy, 0.0))


def union_box(words: Sequence[OcrWord]) -> Box:
    """The smallest box around ``words``."""
    left = min(word.box.x for word in words)
    top = min(word.box.y for word in words)
    right = max(word.box.x + word.box.width for word in words)
    bottom = max(word.box.y + word.box.height for word in words)
    return Box(x=left, y=top, width=right - left, height=bottom - top)


def _key(text: str) -> str:
    return "".join(character for character in text.casefold() if character.isalnum())
