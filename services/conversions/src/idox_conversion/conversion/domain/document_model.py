"""What the model reads from a page: blocks in reading order (IDP-03 ch. 3.4, "the document model").

Pure data, no framework (DOM-01). The model's own answer is validated against a schema in the model adapter and turned
into these (LLM-03); the layers above never see the model's raw answer.
"""

from dataclasses import dataclass
from enum import StrEnum


class TextKind(StrEnum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    CAPTION = "caption"
    LIST = "list"
    IMAGE = "image"  # text the model read out of a picture


@dataclass(frozen=True, slots=True, kw_only=True)
class TextBlock:
    """A run of text: a heading, a paragraph, a caption or a list."""

    kind: TextKind
    text: str


@dataclass(frozen=True, slots=True, kw_only=True)
class TableBlock:
    """A table: a header row (possibly empty) and the data rows, every row as wide as ``columns``."""

    header: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    columns: int


Block = TextBlock | TableBlock


@dataclass(frozen=True, slots=True, kw_only=True)
class Page:
    """One page's blocks in reading order."""

    blocks: tuple[Block, ...]

    @property
    def tables(self) -> tuple[TableBlock, ...]:
        return tuple(block for block in self.blocks if isinstance(block, TableBlock))
