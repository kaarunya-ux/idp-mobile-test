"""Outputs of the conversion use cases (standard ch. 1.2: ``*Result``)."""

from dataclasses import dataclass
from pathlib import Path

from conversion.conversion.domain.route import Route


@dataclass(frozen=True, slots=True, kw_only=True)
class ConvertFileResult:
    """The route that ran, the files it wrote, and the pages it processed (feeds ``usage.pages``, IDP-00 ch. 7.6)."""

    route: Route
    outputs: tuple[Path, ...]
    page_count: int
