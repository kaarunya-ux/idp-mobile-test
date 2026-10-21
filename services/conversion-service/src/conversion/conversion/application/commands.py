"""Inputs to the conversion use cases (standard ch. 1.2: ``*Command``)."""

from dataclasses import dataclass, field
from pathlib import Path

from conversion.conversion.domain.formats import Format
from conversion.conversion.domain.options import ConversionOptions
from conversion.conversion.domain.route_planner import OcrMode


@dataclass(frozen=True, slots=True, kw_only=True)
class ConvertFileCommand:
    """Convert local input files from ``source`` to ``target``, writing the results into ``output_dir``."""

    source: Format
    target: Format
    inputs: tuple[Path, ...]
    output_dir: Path
    options: ConversionOptions = field(default_factory=ConversionOptions)
    pdf_is_scanned: bool = False
    ocr: OcrMode = OcrMode.AUTO
