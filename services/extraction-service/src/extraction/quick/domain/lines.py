"""The generic read over OCR's lines (ADR 0015): OCR reads, the model only names which lines hold which field.

The lines are numbered across the uploaded pages in reading order. The model answers key, label, type and line
numbers, never text, so it writes a fraction of what it writes when it reads the picture, and each value is OCR's
exact text with OCR's box. When a label is printed on the value's line ("DL No: TS0100"), the label's words are left
out of the value and its box.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from statistics import fmean

from extraction.quick.domain.locate import union_box
from extraction.quick.domain.models import ExtractedField, QuickResult, ReadBy, Table
from extraction.quick.domain.placed import PlacedBox
from idp_ocr_engines.model import OcrPage, OcrWord

_SEPARATORS = ":;-=|"


@dataclass(frozen=True, slots=True, kw_only=True)
class Line:
    """One OCR line: its number in the request, its page (from 1, upload order) and its words left to right."""

    number: int
    page: int
    words: tuple[OcrWord, ...]

    @property
    def text(self) -> str:
        return " ".join(word.text for word in self.words)


@dataclass(frozen=True, slots=True, kw_only=True)
class LinesField:
    """A field as the model named it: the lines of its value, or for a table rows of cells of line numbers."""

    key: str
    label: str
    type: str  # string, number, date or table
    lines: tuple[int, ...] = ()
    rows: tuple[tuple[tuple[int, ...], ...], ...] | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class LinesReading:
    document_type: str | None
    document_confidence: float | None
    fields: Sequence[LinesField]
    model_id: str
    prompt_version: str


def number_lines(pages: Sequence[OcrPage]) -> list[Line]:
    """Every line on every page, numbered from 0 in reading order (pages in upload order, lines as OCR ordered them)."""
    lines: list[Line] = []
    for page_number, page in enumerate(pages, start=1):
        grouped: dict[int, list[OcrWord]] = {}
        for word in page.words:
            grouped.setdefault(word.line_id, []).append(word)
        for words in grouped.values():
            ordered = tuple(sorted(words, key=lambda word: word.box.x))
            lines.append(Line(number=len(lines), page=page_number, words=ordered))
    return lines


def result_from_lines(reading: LinesReading, lines: Sequence[Line]) -> QuickResult:
    """The model's answer as the route's result: each value OCR's text from its lines, with its box."""
    by_number = {line.number: line for line in lines}
    return QuickResult(
        document_type=reading.document_type,
        document_confidence=reading.document_confidence,
        fields=[_field(found, by_number) for found in reading.fields],
        read_by=ReadBy.AI,
        model_id=reading.model_id,
        prompt_version=reading.prompt_version,
    )


def _field(found: LinesField, lines: dict[int, Line]) -> ExtractedField:
    if found.rows is not None:
        table = [[_text(cell, lines) for cell in row] for row in found.rows]
        value = Table(headers=table[0], rows=table[1:]) if table else None
        return ExtractedField(key=found.key, label=found.label, type="table", value=value, confidence=None)
    printed = [word for n in found.lines if n in lines for word in lines[n].words]
    words, label = _without_label(printed, found.label), found.label
    if printed and not words:  # the model gave the value as its label ("5640 2947 6217"): the line is the value
        words, label = printed, found.key.replace("_", " ").capitalize()
    if not words:
        return ExtractedField(key=found.key, label=label, type=found.type, value=None, confidence=None)
    page = lines[next(n for n in found.lines if n in lines)].page
    on_page = [word for n in found.lines if n in lines and lines[n].page == page for word in lines[n].words]
    boxed = [word for word in on_page if word in words] or words
    return ExtractedField(
        key=found.key,
        label=label,
        type=found.type,
        value=" ".join(word.text for word in words).lstrip(_SEPARATORS + " ").rstrip(":; ") or None,
        confidence=round(fmean(word.confidence for word in words), 3),
        box=PlacedBox(page=page, box=union_box(boxed)),
    )


def _text(cell: Sequence[int], lines: dict[int, Line]) -> str:
    return " ".join(lines[n].text for n in cell if n in lines)


def _without_label(words: list[OcrWord], label: str) -> list[OcrWord]:
    """The words after a printed label at the start ("Name:" "JOHN" -> "JOHN", "Email:a@b.c" -> "a@b.c"); all of them
    when the line doesn't start with the label."""
    wanted, spelled = _key(label), ""
    for index, word in enumerate(words):
        own = _key(word.text)
        if not wanted or not (wanted.startswith(spelled + own) or (spelled + own).startswith(wanted)):
            return words
        if len(spelled + own) > len(wanted):  # the label runs into the value inside this word
            tail = _after_letters(word.text, len(wanted) - len(spelled))
            return [replace(word, text=tail), *words[index + 1 :]] if tail else words[index + 1 :]
        spelled += own
        if spelled == wanted:
            return [rest for rest in words[index + 1 :] if _key(rest.text)]
    return words


def _after_letters(text: str, count: int) -> str:
    """``text`` after its first ``count`` letters and digits, without the separators that follow them."""
    seen = 0
    for position, character in enumerate(text):
        if seen == count:
            return text[position:].lstrip(_SEPARATORS + " ")
        seen += character.isalnum()
    return ""


def _key(text: str) -> str:
    return "".join(character for character in text.casefold() if character.isalnum())
