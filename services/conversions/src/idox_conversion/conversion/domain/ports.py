"""What the domain needs from outside, as ports the infrastructure implements (standard ch. 3.2: domain/ports.py)."""

from dataclasses import dataclass
from typing import Protocol

from idox_conversion.conversion.domain.document_model import Page


@dataclass(frozen=True, slots=True, kw_only=True)
class Provenance:
    """Which model and prompt produced a reading. Written on every output so a result can be traced (ML-04, LLM-02)."""

    model_name: str
    model_version: str
    prompt_name: str
    prompt_version: int


@dataclass(frozen=True, slots=True, kw_only=True)
class PageReading:
    """What the model read from one page, and what it cost."""

    page: Page
    provenance: Provenance
    prompt_tokens: int
    completion_tokens: int


class PageReader(Protocol):
    """Reads one page picture into blocks. The only way business code reaches the model (LLM-01)."""

    def read_page(self, picture: bytes, *, short_page: bool = False) -> PageReading:
        """Raise ``ModelUnavailableError`` or ``ModelAnswerInvalidError`` when the page cannot be read."""
        ...
