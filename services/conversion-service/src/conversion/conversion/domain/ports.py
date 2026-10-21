"""What the conversion rules need from outside, described in domain terms (standard ch. 7, ports).

Infrastructure adapters implement these protocols; the application layer depends only on the protocols, so it
never imports Pillow, PDFium or LibreOffice code directly.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from conversion.conversion.domain.options import ConversionOptions
from conversion.conversion.domain.route import Converter, Step


@dataclass(frozen=True, slots=True, kw_only=True)
class StepRequest:
    """One step to run on files in a local working folder."""

    step: Step
    inputs: tuple[Path, ...]  # usually one file; several only when images become one PDF or deck
    output_dir: Path
    options: ConversionOptions


@dataclass(frozen=True, slots=True, kw_only=True)
class StepResult:
    """What a step wrote, and how many pages or images it processed."""

    outputs: tuple[Path, ...]
    page_count: int


class ConverterPort(Protocol):
    """Runs one kind of conversion step."""

    @property
    def converter(self) -> Converter:
        """Which step this implementation runs."""
        ...

    def convert(self, request: StepRequest) -> StepResult:
        """Write the step's output into ``request.output_dir``. Raise a ``DomainError`` when the input is bad."""
        ...


class ConverterLookupPort(Protocol):
    """Finds the implementation for a kind of step."""

    def get(self, converter: Converter) -> ConverterPort:
        """Return the implementation of ``converter``; raise ``LookupError`` if this process has none."""
        ...
