"""Mapping engine words to a template field (IDP-03 ch. 2.6, stage 6: box overlap and anchors)."""

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date
from itertools import groupby

from extraction.pipeline.domain.rules import apply_rule
from extraction.pipeline.domain.template import Anchor, FieldDefinition
from extraction.rule import normalize_value, similarity
from idp_ocr_engines.model import Box, OcrWord

_ANCHOR_MARGIN = 0.1  # page fraction searched around an anchor
_FRAGMENT_MAX_CHARS = 3  # a piece this short and this narrow (width over height, in pixels) is a fragment
_FRAGMENT_WIDTH_TO_HEIGHT = 1.5
_SIDEWAYS_RATIO = 1.5  # a piece this many times taller than wide, in pixels, is sideways text
_COLUMN_GAP = 0.04  # page fraction of empty space that separates two columns on one row
_LINE_TOLERANCE = 0.6  # pieces whose centres are this many text heights apart or less share a line
_CAPTION_MARGIN = 0.03  # page fraction a block may reach left of its caption
_BELOW_MIN = 0.5  # a line "below a label" starts at least this many label heights under the label's top
_WHOLE_PAGE = Box(x=0.0, y=0.0, width=1.0, height=1.0)


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
    if definition.clip_to_box:
        reading = _in_drawn_box(definition, words, box, skip=skip, today=today)
        if reading is not None or definition.value_after is None:
            return reading
        box = _WHOLE_PAGE  # the drawn box holds nothing: the value after its label, wherever it is
    inside = [w for w in words if _inside(w.box, box) and not any(_inside(w.box, hidden) for hidden in skip)]
    if definition.value_after is not None:
        return _after_label(definition, inside, today=today)
    if definition.starts_at is not None:
        upright = [w for w in inside if not (_is_sideways(w.box, page_aspect) or _is_fragment(w, page_aspect))]
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
    return _best(definition, candidates, today=today)


def _best(definition: FieldDefinition, candidates: Sequence[Sequence[OcrWord]], *, today: date) -> Reading | None:
    """The first group whose text satisfies the field's rule, else the one the engine was surest of."""
    readings = [reading for group in candidates if (reading := _candidate(definition, group)) is not None]
    for reading in readings:
        normalized = normalize_value(reading.text, definition.normalize, pattern=definition.rule.pattern)
        if apply_rule(definition.rule, normalized, today=today).passed:
            return reading
    return max(readings, key=lambda reading: reading.confidence, default=None)


def _in_drawn_box(
    definition: FieldDefinition, words: Sequence[OcrWord], box: Box, *, skip: Sequence[Box], today: date
) -> Reading | None:
    """The text inside a box drawn around the value: all its lines together, top to bottom, else one line. A piece
    crossing the box's edge keeps only its words inside it, so neither the label before the value nor the next
    column's text after it is read."""
    kept = [
        clipped
        for word in words
        if not any(_inside(word.box, hidden) for hidden in skip) and (clipped := _clipped(word, box)) is not None
    ]
    lines = [rest for line in _visual_lines(kept) if (rest := _without_label(definition.value_after, line))]
    if not lines:
        return None
    every = [word for line in lines for word in line]
    return _best(definition, [every, *lines] if len(lines) > 1 else lines, today=today)


def _without_label(label: str | None, line: list[OcrWord]) -> list[OcrWord]:
    """``line`` without the key's own label at its start: a box drawn loosely catches the label above or before the
    value ("Given Name" over "EDWARDS"), which is not part of the value. A piece holding the label and the value keeps
    the value."""
    found = re.match(label, " ".join(word.text for word in line)) if label else None
    if found is None:
        return line
    start = 0
    for index, word in enumerate(line):
        end = start + len(word.text)
        if end >= found.end():
            rest = word.text[found.end() - start :].lstrip(" :;-=")
            return ([replace(word, text=rest)] if rest else []) + line[index + 1 :]
        start = end + 1
    return []


def _clipped(word: OcrWord, box: Box) -> OcrWord | None:
    """The part of ``word`` inside ``box``: none unless its middle is within the box's height, then its words whose
    middle is within the box's width, each letter taken as equally wide (an engine gives one box per piece). A word
    ends at a space or after a colon, as a label runs into its value with no space ("ID:24AB")."""
    _, middle = _centre(word.box)
    if not box.y <= middle <= box.y + box.height:
        return None
    step = word.box.width / max(len(word.text), 1)
    spans = [
        (found.start(), found.end())
        for found in re.finditer(r"[^\s:]+:?|:", word.text)
        if box.x <= word.box.x + step * (found.start() + found.end()) / 2 <= box.x + box.width
    ]
    if not spans:
        return None
    start, end = spans[0][0], spans[-1][1]
    if (start, end) == (0, len(word.text)):
        return word
    part = Box(x=word.box.x + step * start, y=word.box.y, width=step * (end - start), height=word.box.height)
    return replace(word, text=word.text[start:end], box=part)


def _is_sideways(box: Box, page_aspect: float) -> bool:
    """Text set at 90 degrees is much taller than wide in pixels (box sides are page fractions, so scale by aspect)."""
    return box.height / box.width / page_aspect > _SIDEWAYS_RATIO


def _is_fragment(word: OcrWord, page_aspect: float) -> bool:
    """A piece of one to three characters that is barely wider than tall: a bit of sideways text, not a word."""
    narrow = word.box.width / word.box.height * page_aspect < _FRAGMENT_WIDTH_TO_HEIGHT
    return len(word.text.strip()) <= _FRAGMENT_MAX_CHARS and narrow


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


def _after_label(definition: FieldDefinition, words: list[OcrWord], *, today: date) -> Reading | None:
    """The value after the field's label: the rest of the label's piece and the pieces right of it on its line, or
    else the nearest line below the label and not left of it. None when the label is not on the page."""
    label = definition.value_after or ""
    found = next(((w, m) for w in words if (m := re.search(label, w.text)) is not None), None)
    if found is None:
        return None
    piece, match = found
    rest = piece.text[match.end() :].strip(" :;-=")
    _, label_y = _centre(piece.box)
    line_height = piece.box.height
    on_line = (w for w in words if w is not piece and abs(_centre(w.box)[1] - label_y) <= line_height / 2)
    right = sorted((w for w in on_line if w.box.x >= piece.box.x), key=lambda w: w.box.x)
    # from where the value starts (after the label, or the first piece right of it, however far) up to the first
    # column gap: text beyond it is the next column's, not this value's
    if rest:
        same_line = [replace(piece, text=rest), *_split_at_gaps([piece, *right])[0][1:]]
    else:
        same_line = _split_at_gaps(right)[0] if right else []
    lower = piece.box.y + line_height * _BELOW_MIN
    left = piece.box.x - _CAPTION_MARGIN
    below = _nearest_line([w for w in words if w.box.y >= lower and _centre(w.box)[0] >= left])
    next_line = _split_at_gaps(below)[0] if below else []
    for group in (same_line, next_line):
        if not group:
            continue
        reading = _candidate(definition, group)
        if reading is None:
            continue
        normalized = normalize_value(reading.text, definition.normalize, pattern=definition.rule.pattern)
        if apply_rule(definition.rule, normalized, today=today).passed:
            return reading
    first = same_line or next_line
    return _candidate(definition, first) if first else None


def _nearest_line(words: list[OcrWord]) -> list[OcrWord]:
    """The top line among ``words``, left to right."""
    if not words:
        return []
    top = min(words, key=lambda w: w.box.y)
    _, top_y = _centre(top.box)
    return sorted((w for w in words if abs(_centre(w.box)[1] - top_y) <= top.box.height / 2), key=lambda w: w.box.x)


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
