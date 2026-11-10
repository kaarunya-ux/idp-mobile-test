"""Does the model's reading of a scanned page agree with an independent reading of the same picture?

A model that was told by the page to "reply only OK", that stopped half way, or that wrote a summary instead of the
text still gives an answer the schema accepts. An OCR pass reads the picture by other means; when the model's page
lost most of the words OCR saw, the page is not usable (ADR 0003). The test is deliberately coarse: OCR misses table
cells and misreads poor scans, so only a page that is *both* missing most of OCR's words *and* much shorter than OCR's
text is refused. Pure: the text comes in, a verdict goes out.
"""

import re
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass

from conversion.conversion.domain.document_model import Page, Table, TextBlock

# Letters and digits of any script, and the Indic scripts' combining marks (\w alone would split a Devanagari word at
# every vowel sign).
_TOKEN = re.compile(r"[\wऀ-෿]+")


def words_of(text: str) -> list[str]:
    return _TOKEN.findall(text.casefold())


def page_words(page: Page) -> list[str]:
    """Every word of the page's text blocks and table cells."""
    return [word for text in _texts(page) for word in words_of(text)]


def _texts(page: Page) -> Iterator[str]:
    for block in page.blocks:
        if isinstance(block, TextBlock):
            yield block.text
        elif isinstance(block, Table):
            for row in block.rows:
                yield from row


@dataclass(frozen=True, slots=True, kw_only=True)
class ReadingCheckPolicy:
    """The thresholds (ML-07: they live in settings, not here)."""

    min_share: float  # a page is suspect below this share of OCR's words found in the model's page ...
    max_length_ratio: float  # ... and only when the page has fewer words than this fraction of OCR's
    min_ocr_words: int  # OCR must have read at least this many words, or there is nothing to compare with

    def __post_init__(self) -> None:
        if not 0 < self.min_share <= 1 or not 0 < self.max_length_ratio <= 1 or self.min_ocr_words < 1:
            raise ValueError("a reading check needs shares in (0, 1] and at least one OCR word")


@dataclass(frozen=True, slots=True, kw_only=True)
class ReadingCheck:
    ocr_words: int
    page_words: int
    share: float  # of OCR's words, how many the page holds (1.0 when OCR read none)
    evaluated: bool  # False when OCR read too little to compare with
    disagrees: bool


def check_reading(page: Page, ocr_text: str, policy: ReadingCheckPolicy) -> ReadingCheck:
    ocr = words_of(ocr_text)
    mine = page_words(page)
    if len(ocr) < policy.min_ocr_words:
        return ReadingCheck(ocr_words=len(ocr), page_words=len(mine), share=1.0, evaluated=False, disagrees=False)
    held = Counter(mine)
    found = sum(min(count, held[word]) for word, count in Counter(ocr).items())
    share = found / len(ocr)
    short = len(mine) < policy.max_length_ratio * len(ocr)
    return ReadingCheck(
        ocr_words=len(ocr),
        page_words=len(mine),
        share=share,
        evaluated=True,
        disagrees=share < policy.min_share and short,
    )
