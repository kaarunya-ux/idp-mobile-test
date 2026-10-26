"""The document model: the data hub every format is read into and written out of (IDP-03 ch. 3.4).

A document is pages; a page holds blocks in reading order; a block is text (heading, paragraph, list item or
caption) or a table. Cells are text: typing numbers is the writer's job (``cell_values``), so nothing is
reinterpreted on the way through. Page positions and images arrive with the PDF readers (step 3c-3).
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Self

from conversion.conversion.domain.exceptions import InvalidDocumentModelError, LimitExceededError

MAX_HEADING_LEVEL = 6


class TextKind(StrEnum):
    HEADING = "HEADING"
    PARAGRAPH = "PARAGRAPH"
    LIST_ITEM = "LIST_ITEM"
    CAPTION = "CAPTION"


class Alignment(StrEnum):
    LEFT = "LEFT"
    CENTER = "CENTER"
    RIGHT = "RIGHT"


@dataclass(frozen=True, slots=True, kw_only=True)
class TextBlock:
    """A run of text with its role and how it looks. ``level`` is the heading level or list depth, from 1."""

    text: str
    kind: TextKind = TextKind.PARAGRAPH
    level: int = 1
    alignment: Alignment = Alignment.LEFT
    bold: bool = False

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise InvalidDocumentModelError("a text block needs text")
        if not 1 <= self.level <= MAX_HEADING_LEVEL:
            raise InvalidDocumentModelError(f"level {self.level} is outside 1-{MAX_HEADING_LEVEL}")


@dataclass(frozen=True, slots=True, kw_only=True)
class Table:
    """A rectangular grid of text cells. The first ``header_rows`` rows are headers."""

    rows: tuple[tuple[str, ...], ...]
    header_rows: int = 0

    def __post_init__(self) -> None:
        if not self.rows or not self.rows[0]:
            raise InvalidDocumentModelError("a table needs at least one cell")
        if any(len(row) != len(self.rows[0]) for row in self.rows):
            raise InvalidDocumentModelError("every row of a table must have the same number of cells")
        if not 0 <= self.header_rows <= len(self.rows):
            raise InvalidDocumentModelError(f"{self.header_rows} header rows in a {len(self.rows)}-row table")

    @classmethod
    def from_ragged(cls, rows: Iterable[Sequence[str]], *, header_rows: int = 0) -> Self:
        """Pad short rows with empty cells, as real tables often have rows narrower than their header."""
        materialised = [tuple(row) for row in rows]
        width = max((len(row) for row in materialised), default=0)
        return cls(rows=tuple(row + ("",) * (width - len(row)) for row in materialised), header_rows=header_rows)

    @property
    def column_count(self) -> int:
        return len(self.rows[0])

    @property
    def body(self) -> tuple[tuple[str, ...], ...]:
        return self.rows[self.header_rows :]


type Block = TextBlock | Table


@dataclass(frozen=True, slots=True, kw_only=True)
class Page:
    """One page, sheet or slide. ``name`` is a sheet name or slide title when the source has one."""

    blocks: tuple[Block, ...]
    name: str | None = None

    @property
    def tables(self) -> list[Table]:
        return [block for block in self.blocks if isinstance(block, Table)]


@dataclass(frozen=True, slots=True, kw_only=True)
class DocumentModel:
    pages: tuple[Page, ...]

    @property
    def tables(self) -> list[Table]:
        """Every table, in reading order."""
        return [table for page in self.pages for table in page.tables]


def check_table_sizes(model: DocumentModel, max_rows: int) -> None:
    """Refuse tables too long for a document or a deck; a spreadsheet target suits them instead."""
    if any(len(table.rows) > max_rows for table in model.tables):
        raise LimitExceededError(
            f"A table has more than {max_rows:,} rows, too many for a document. Convert it to XLSX or CSV instead."
        )
