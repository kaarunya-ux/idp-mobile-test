"""The template definition, schema version 1 (IDP-03 ch. 2.5): what to look for on a document and how to accept it.

Plain frozen dataclasses with no framework. Parsing the stored JSON into these is the template feature's job; the
pipeline only reads them. Boxes are fractions of the page (top-left origin).
"""

import re
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum

from extraction.rule.normalizers import Normalizer
from idp_ocr_engines.model import Box


class FieldType(StrEnum):
    """The subset of IDP-00 ch. 7.8 field types used by the standard identity templates."""

    TEXT = "TEXT"
    NAME = "NAME"
    ID = "ID"
    DATE = "DATE"
    NUMBER = "NUMBER"
    ADDRESS = "ADDRESS"


class RuleKind(StrEnum):
    """``rule.kind``: REGEX and DATE are generic; CUSTOM names a validator in ``rule/``."""

    REGEX = "REGEX"
    DATE = "DATE"
    CUSTOM = "CUSTOM"


class AcceptMode(StrEnum):
    """``accept.mode``."""

    EXACT = "EXACT"
    SIMILARITY = "SIMILARITY"


@dataclass(frozen=True, slots=True, kw_only=True)
class RuleSpec:
    """Which rule judges the field. ``latest`` of ``None`` means today (the caller supplies it, DOM-05)."""

    id: str
    kind: RuleKind
    pattern: str | None = None  # REGEX: the check itself. Others: the value shape, which OCR_CONFUSABLES_ALNUM reads
    formats: tuple[str, ...] = ()
    earliest: date = date(1900, 1, 1)
    latest: date | None = None
    validator: str | None = None  # CUSTOM only: "PAN" or "AADHAAR"


@dataclass(frozen=True, slots=True, kw_only=True)
class Accept:
    """How two readings count as the same. ``threshold`` of ``None`` uses ``VERIFY__NAME_SIMILARITY``."""

    mode: AcceptMode = AcceptMode.EXACT
    threshold: float | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class Mask:
    """``mask``: every letter and digit except the last ``keep_last`` is replaced by ``char``."""

    keep_last: int
    char: str = "X"


@dataclass(frozen=True, slots=True, kw_only=True)
class Anchor:
    """Printed text that fixes the layout. ``region`` is where it normally sits; fields shift with it."""

    id: str
    text: str
    region: Box
    min_similarity: float = 0.8


@dataclass(frozen=True, slots=True, kw_only=True)
class FieldDefinition:
    """One field to read: where it is, how to clean it, which rule judges it, and how readings are compared.

    ``labels`` is a regular expression for printed captions inside the region (``Father's Name``) that are dropped
    before reading. ``extract`` cuts the value out of surrounding text (a number within a line); when it has a
    capture group, the group is the value. ``above`` is a regular expression for a line the value sits just above
    (the name above the date of birth): only the ``max_lines_above`` lines before it are read, nearest first.
    ``excludes`` names earlier fields whose text is left out of this one (the address must not contain the name).
    ``keep`` is a regular expression a word must match to be read at all (the Hindi address keeps only Devanagari).
    ``starts_at`` is a regular expression for the caption that opens the value (``Address:``): when it is found, only
    words below it and not left of it are read, which keeps the address apart from text in other columns or cards.
    ``language`` is the ISO 639-1 code an engine must read to read this field; other engines skip it.
    """

    key: str
    label: str
    type: FieldType
    box: Box
    rule: RuleSpec
    normalize: tuple[Normalizer, ...]
    required: bool = True
    relative_to: str | None = None
    labels: str | None = None
    extract: str | None = None
    above: str | None = None
    max_lines_above: int = 3
    excludes: tuple[str, ...] = ()
    keep: str | None = None
    starts_at: str | None = None
    language: str = "en"
    accept: Accept = field(default_factory=Accept)
    mask: Mask | None = None

    def __post_init__(self) -> None:
        for pattern in (self.labels, self.extract, self.above, self.keep, self.starts_at):
            if pattern is not None:
                re.compile(pattern)


@dataclass(frozen=True, slots=True, kw_only=True)
class TemplateDefinition:
    """A template version: its identity, how to recognise the document, and its fields."""

    template_id: str
    version: int
    scope: str  # "STANDARD" or "ACCOUNT"
    document_type: str
    aspect_ratio: float
    aspect_tolerance: float
    keywords: tuple[str, ...]  # regular expressions searched in the page text
    min_keyword_hits: int
    anchors: tuple[Anchor, ...]
    fields: tuple[FieldDefinition, ...]
