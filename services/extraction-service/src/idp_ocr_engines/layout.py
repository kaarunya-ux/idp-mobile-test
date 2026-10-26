"""Giving recognised text pieces their line and block ids, and putting them in reading order (IDP-03 ch. 2.8).

Engines return text pieces with boxes. A *line* is the pieces on one row that are close enough to read together:
their vertical centres line up and the gap between neighbours is only a few text heights, so the two panels of a
side-by-side card stay separate lines. A *block* is a run of lines that sit close together and overlap horizontally,
such as an address. Both rules are deterministic (ML-05).
"""

from collections.abc import Sequence
from dataclasses import dataclass

from idp_ocr_engines.model import Box, OcrWord

_SAME_LINE_FRACTION = 0.5  # centres closer than half the taller piece's height are on one row
_MAX_LINE_GAP_HEIGHTS = 4.0  # pieces on one row further apart than this many text heights are separate lines
_BLOCK_GAP_LINES = 1.0  # a gap bigger than one line height starts a new block


@dataclass(frozen=True, slots=True, kw_only=True)
class RecognizedPiece:
    """What an engine read before ids are assigned: text, where, and confidence (0 to 1)."""

    text: str
    box: Box
    confidence: float


@dataclass(slots=True)
class _Line:
    pieces: list[RecognizedPiece]
    center_y: float
    height: float

    @property
    def top(self) -> float:
        return min(piece.box.y for piece in self.pieces)

    @property
    def bottom(self) -> float:
        return max(piece.box.y + piece.box.height for piece in self.pieces)

    @property
    def left(self) -> float:
        return min(piece.box.x for piece in self.pieces)

    @property
    def right(self) -> float:
        return max(piece.box.x + piece.box.width for piece in self.pieces)


def assign_line_and_block_ids(pieces: Sequence[RecognizedPiece]) -> tuple[OcrWord, ...]:
    """Turn pieces into ``OcrWord`` objects in reading order, numbering lines and blocks from 0."""
    lines = _group_into_lines(pieces)
    words: list[OcrWord] = []
    block_id = -1
    previous: _Line | None = None
    for line_id, line in enumerate(lines):
        if previous is None or not _continues_block(previous, line):
            block_id += 1
        words.extend(
            OcrWord(text=piece.text, box=piece.box, confidence=piece.confidence, line_id=line_id, block_id=block_id)
            for piece in sorted(line.pieces, key=_across_key)
        )
        previous = line
    return tuple(words)


def _across_key(piece: RecognizedPiece) -> tuple[float, float, float, float, str, float]:
    """Left to right, with every other attribute as a tie-breaker so the order never depends on arrival order."""
    return (piece.box.x, piece.box.width, piece.box.height, piece.box.y, piece.text, piece.confidence)


def _down_key(piece: RecognizedPiece) -> tuple[float, float, float, float, float, str, float]:
    """Top to bottom by vertical centre, then left to right, then every other attribute (ML-05: deterministic)."""
    return (
        piece.box.y + piece.box.height / 2,
        piece.box.x,
        piece.box.width,
        piece.box.height,
        piece.box.y,
        piece.text,
        piece.confidence,
    )


def _group_into_lines(pieces: Sequence[RecognizedPiece]) -> list[_Line]:
    lines: list[_Line] = []
    for piece in sorted(pieces, key=_down_key):
        center = piece.box.y + piece.box.height / 2
        target = next((line for line in lines if _belongs_to(line, piece, center)), None)
        if target is None:
            lines.append(_Line(pieces=[piece], center_y=center, height=piece.box.height))
            continue
        target.pieces.append(piece)
        count = len(target.pieces)
        target.center_y += (center - target.center_y) / count
        target.height = max(target.height, piece.box.height)
    return sorted(lines, key=lambda line: (line.center_y, line.left))


def _belongs_to(line: _Line, piece: RecognizedPiece, center: float) -> bool:
    """Whether the piece is on the line's row and close enough to its neighbours to be read with them."""
    height = max(piece.box.height, line.height)
    same_row = abs(center - line.center_y) < _SAME_LINE_FRACTION * height
    gap = max(0.0, piece.box.x - line.right, line.left - (piece.box.x + piece.box.width))
    return same_row and gap <= _MAX_LINE_GAP_HEIGHTS * height


def _continues_block(previous: _Line, line: _Line) -> bool:
    close = line.top - previous.bottom <= _BLOCK_GAP_LINES * max(previous.height, line.height)
    overlaps = line.left < previous.right and previous.left < line.right
    return close and overlaps
