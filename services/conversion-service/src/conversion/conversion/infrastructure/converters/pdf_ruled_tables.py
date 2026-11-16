"""Tables drawn with a rule under each row and no column lines, as browsers print Markdown and HTML tables.

pdfplumber's own table finder needs the grid's lines in both directions, so such a table came out as scrambled text:
each row's cells read across as one line. Their rules give them away: each row's rule is drawn one segment per cell,
end to end, so the joints between the segments are the column edges, and the same joints rule after rule make a
table. The header sits above the first rule and the last row below the last one; each reaches as far as the text runs
on without a paragraph's gap.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import groupby, pairwise
from typing import Any

from pdfplumber.page import Page as PdfPage

_RULE_MAX_HEIGHT_PT = 2.0  # a filled rectangle or a line this thin is a rule
_JOINT_TOLERANCE_PT = 1.0  # segments this close end to end are one rule; joints this close are one column edge
_MIN_RULES = 2  # one segmented rule alone may be a form's blanks; two with the same joints are a table


@dataclass(frozen=True, slots=True, kw_only=True)
class RuledTable:
    columns: tuple[float, ...]  # each column edge, left to right, in points
    rules: tuple[float, ...]  # each row rule, top to bottom


def ruled_tables(page: PdfPage) -> list[RuledTable]:
    """The page's tables drawn with segmented row rules, top to bottom."""
    segments = sorted(_segments(page))
    rules: list[tuple[float, tuple[float, ...]]] = []
    for top, group in groupby(segments, key=lambda segment: segment[0]):
        joints = _joints([(x0, x1) for _, x0, x1 in group])
        if joints is not None:
            rules.append((top, joints))
    return [
        RuledTable(columns=run[0][1], rules=tuple(top for top, _ in run))
        for run in _runs(rules)
        if len(run) >= _MIN_RULES
    ]


def reach(table: RuledTable, lines: Sequence[tuple[float, float]], gap_ratio: float) -> tuple[float, float]:
    """How far the table runs above its first rule and below its last: over the text lines (``top``, ``bottom``)
    that follow each other with no gap wider than ``gap_ratio`` of a line's height."""
    top = table.rules[0]
    for line_top, line_bottom in sorted((line for line in lines if line[1] <= top), key=lambda line: -line[1]):
        if top - line_bottom > (line_bottom - line_top) * gap_ratio:
            break
        top = min(top, line_top)
    bottom = table.rules[-1]
    for line_top, line_bottom in sorted(line for line in lines if line[0] >= bottom):
        if line_top - bottom > (line_bottom - line_top) * gap_ratio:
            break
        bottom = max(bottom, line_bottom)
    return top, bottom


def _segments(page: PdfPage) -> list[tuple[float, float, float]]:
    """Every thin horizontal mark on the page as (top, x0, x1), its top rounded so one rule's segments group."""
    marks: list[dict[str, Any]] = [*page.rects, *page.lines]
    return [
        (round(float(mark["top"]), 1), float(mark["x0"]), float(mark["x1"]))
        for mark in marks
        if float(mark["bottom"]) - float(mark["top"]) <= _RULE_MAX_HEIGHT_PT
        and float(mark["x1"]) - float(mark["x0"]) > _RULE_MAX_HEIGHT_PT
    ]


def _joints(spans: list[tuple[float, float]]) -> tuple[float, ...] | None:
    """The column edges of one rule drawn as two or more segments end to end; None for anything else. A segment drawn
    twice counts once (a header repeated on a table's next page has its rule drawn twice)."""
    spans = sorted(set(spans))
    if len(spans) < _MIN_RULES or any(
        abs(right - next_left) > _JOINT_TOLERANCE_PT for (_, right), (next_left, _) in pairwise(spans)
    ):
        return None
    return (spans[0][0], *(right for _, right in spans))


def _runs(rules: list[tuple[float, tuple[float, ...]]]) -> list[list[tuple[float, tuple[float, ...]]]]:
    """Consecutive rules with the same column edges."""
    runs: list[list[tuple[float, tuple[float, ...]]]] = []
    for rule in rules:
        if runs and _same_columns(runs[-1][-1][1], rule[1]):
            runs[-1].append(rule)
        else:
            runs.append([rule])
    return runs


def _same_columns(left: tuple[float, ...], right: tuple[float, ...]) -> bool:
    return len(left) == len(right) and all(abs(a - b) <= _JOINT_TOLERANCE_PT for a, b in zip(left, right, strict=True))
