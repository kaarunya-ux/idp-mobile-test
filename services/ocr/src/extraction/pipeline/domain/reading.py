"""Mapping engine words to a template field (IDP-03 ch. 2.6, stage 6: box overlap and anchors)."""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from itertools import groupby

from extraction.pipeline.domain.rules import apply_rule
from extraction.pipeline.domain.template import Anchor, FieldDefinition
from extraction.rule import normalize_value, similarity
from idp_ocr_engines.model import Box, OcrWord

_ANCHOR_MARGIN = 0.1  # page fraction searched around an anchor
_SIDEWAYS_RATIO = 1.5  # a piece this many times taller than wide, in pixels, is sideways text
_COLUMN_GAP = 0.04  # page fraction of empty space that separates two columns on one row
_LINE_TOLERANCE = 0.6  # pieces whose centres are this many text heights apart or less share a line
_CAPTION_MARGIN = 0.03  # page fraction a block may reach left of its caption


@dataclass(frozen=True, slots=True, kw_only=True)
class Reading:
    """What one engine read for one field: the text, the engine's confidence and where the words are."""

    text: str
    confidence: float
    box: Box


def shift_for(anchor: Anchor, words: Sequence[OcrWord]) -> tuple[float, float] | None:
    """How far the printed anchor sits from its usual place, or ``None`` if it is not found."""
    window = _padded(anchor.region)
    best: OcrWord | None = None
    best_score = anchor.min_similarity
    for word in words:
        if not _inside(word.box, window):
            continue
        score = similarity(word.text.upper(), anchor.text.upper())
        if score >= best_score:
            best, best_score = word, score
    if best is None:
        return None
    return _centre(best.box)[0] - _centre(anchor.region)[0], _centre(best.box)[1] - _centre(anchor.region)[1]


def field_box(definition: FieldDefinition, shifts: dict[str, tuple[float, float]]) -> Box:
    """The field's box, moved with its anchor when the anchor was found, and kept inside the page."""
    box = definition.box
    if definition.relative_to is None or definition.relative_to not in shifts:
        return box
    dx, dy = shifts[definition.relative_to]
    return Box(
        x=min(max(0.0, box.x + dx), 1 - box.width),
        y=min(max(0.0, box.y + dy), 1 - box.height),
        width=box.width,
        height=box.height,
    )


def read_field(
    definition: FieldDefinition,
    words: Sequence[OcrWord],
    box: Box,
    *,
    today: date,
    skip: Sequence[Box] = (),
    page_aspect: float = 1.0,
) -> Reading | None:
    """Pick the text in ``box`` that satisfies the field's rule: one line at a time, then all lines together.

    Words inside any ``skip`` box (text already read as another field) are left out. ``page_aspect`` is the page's
    width over height in pixels, used to tell sideways text from a short caption.
    """
    inside = [w for w in words if _inside(w.box, box) and not any(_inside(w.box, hidden) for hidden in skip)]
    if definition.starts_at is not None:
        upright = [w for w in inside if not _is_sideways(w.box, page_aspect)]
        inside = _from_caption(definition.starts_at, upright)
    if definition.starts_at is not None or definition.above is not None:  # follow where each piece sits
        lines = _visual_lines(inside)
    else:
        lines = [list(group) for _, group in groupby(inside, key=lambda word: word.line_id)]
    if definition.above is not None:  # find the anchor line before captions are dropped: it may be one
        lines = _lines_above(lines, definition.above, definition.max_lines_above)
    lines = [kept for line in lines if (kept := [w for w in line if _is_wanted(definition, w)])]
    inside = [word for line in lines for word in line]
    if not inside:
        return None
    candidates = [*lines, inside] if len(lines) > 1 else lines
    readings = [reading for group in candidates if (reading := _candidate(definition, group)) is not None]
    for reading in readings:
        normalized = normalize_value(reading.text, definition.normalize, pattern=definition.rule.pattern)
        if apply_rule(definition.rule, normalized, today=today).passed:
            return reading
    return max(readings, key=lambda reading: reading.confidence, default=None)


def _is_sideways(box: Box, page_aspect: float) -> bool:
    """Text set at 90 degrees is much taller than wide in pixels (box sides are page fractions, so scale by aspect)."""
    return box.height / box.width / page_aspect > _SIDEWAYS_RATIO


def _visual_lines(words: list[OcrWord]) -> list[list[OcrWord]]:
    """Group pieces into lines by their vertical position (top to bottom), each line left to right."""
    if not words:
        return []
    tolerance = _LINE_TOLERANCE * sorted(word.box.height for word in words)[len(words) // 2]
    lines: list[list[OcrWord]] = []
    for word in sorted(words, key=lambda w: _centre(w.box)[1]):
        if lines and abs(_centre(word.box)[1] - _centre(lines[-1][0].box)[1]) <= tolerance:
            lines[-1].append(word)
        else:
            lines.append([word])
    return [part for line in lines for part in _split_at_gaps(sorted(line, key=lambda w: w.box.x))]


def _split_at_gaps(line: list[OcrWord]) -> list[list[OcrWord]]:
    """Cut a row where the space between two pieces is wide: they belong to different columns or cards."""
    parts = [[line[0]]]
    for word in line[1:]:
        previous = parts[-1][-1].box
        if word.box.x - (previous.x + previous.width) > _COLUMN_GAP:
            parts.append([word])
        else:
            parts[-1].append(word)
    return parts


def _from_caption(caption: str, words: list[OcrWord]) -> list[OcrWord]:
    """Words below the first caption matching ``caption`` and not left of it; all words if there is no such caption."""
    found = next((word for word in words if re.search(caption, word.text) is not None), None)
    if found is None:
        return words
    left = found.box.x - _CAPTION_MARGIN
    return [word for word in words if _centre(word.box)[0] >= left and _centre(word.box)[1] >= found.box.y]


def _lines_above(lines: list[list[OcrWord]], anchor: str, count: int) -> list[list[OcrWord]]:
    """The ``count`` lines just above the first line matching ``anchor`` and in its column, nearest first."""
    for line in lines:
        if re.search(anchor, " ".join(word.text for word in line)) is None:
            continue
        left = min(word.box.x for word in line) - _CAPTION_MARGIN
        right = max(word.box.x + word.box.width for word in line) + _CAPTION_MARGIN
        top = min(word.box.y for word in line)
        above = [
            other
            for other in lines
            if other is not line
            and max(w.box.y + w.box.height / 2 for w in other) < top + (line[0].box.height / 2)
            and min(w.box.x + w.box.width for w in other) > left
            and max(w.box.x for w in other) < right
        ]
        return sorted(above, key=lambda other: -_centre(other[0].box)[1])[:count]
    return []


def cut_value(definition: FieldDefinition, text: str) -> str | None:
    """The value inside ``text``: all of it, or what the field's ``extract`` pattern finds (``None`` if nothing)."""
    if definition.extract is None:
        return text
    found = re.search(definition.extract, text)
    return None if found is None else found.group(found.lastindex or 0)


def _candidate(definition: FieldDefinition, group: Sequence[OcrWord]) -> Reading | None:
    text = cut_value(definition, " ".join(word.text for word in group))
    if text is None:
        return None
    left = min(w.box.x for w in group)
    top = min(w.box.y for w in group)
    right = max(w.box.x + w.box.width for w in group)
    bottom = max(w.box.y + w.box.height for w in group)
    union = Box(x=left, y=top, width=min(right, 1.0) - left, height=min(bottom, 1.0) - top)
    return Reading(text=text, confidence=sum(w.confidence for w in group) / len(group), box=union)


def _is_wanted(definition: FieldDefinition, word: OcrWord) -> bool:
    if definition.labels is not None and re.search(definition.labels, word.text) is not None:
        return False  # a printed caption
    return definition.keep is None or re.search(definition.keep, word.text) is not None


def _padded(box: Box) -> Box:
    """The area searched for an anchor: its usual place plus a margin, so a card that sits lower is still found."""
    left, top = max(0.0, box.x - _ANCHOR_MARGIN), max(0.0, box.y - _ANCHOR_MARGIN)
    right, bottom = min(1.0, box.x + box.width + _ANCHOR_MARGIN), min(1.0, box.y + box.height + _ANCHOR_MARGIN)
    return Box(x=left, y=top, width=right - left, height=bottom - top)


def _centre(box: Box) -> tuple[float, float]:
    return box.x + box.width / 2, box.y + box.height / 2


def _inside(inner: Box, outer: Box) -> bool:
    x, y = _centre(inner)
    return outer.x <= x <= outer.x + outer.width and outer.y <= y <= outer.y + outer.height
