"""Inputs to the conversion use cases (standard ch. 1.2: ``*Command``)."""

from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

from conversion.conversion.domain.events import ItemContext
from conversion.conversion.domain.formats import Format
from conversion.conversion.domain.models import ConversionRequest
from conversion.conversion.domain.options import ConversionOptions
from conversion.conversion.domain.route_planner import OcrMode
from conversion.core.errors import DomainError


@dataclass(frozen=True, slots=True, kw_only=True)
class AcceptConversionCommand:
    """A ``ConversionRequested`` message, checked. ``message_id`` is its ``eventId``: the inbox key."""

    message_id: UUID
    message_type: str
    request: ConversionRequest


@dataclass(frozen=True, slots=True, kw_only=True)
class RejectConversionCommand:
    """A ``ConversionRequested`` whose item is known but whose content can't be used: tell Java it failed."""

    message_id: UUID
    message_type: str
    item: ItemContext
    error: DomainError


@dataclass(frozen=True, slots=True, kw_only=True)
class ProcessItemCommand:
    item_id: UUID


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
