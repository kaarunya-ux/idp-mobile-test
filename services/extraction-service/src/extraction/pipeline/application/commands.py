"""Inputs of the pipeline use cases (standard ch. 1: commands are frozen dataclasses)."""

from dataclasses import dataclass

from extraction.pipeline.domain.models import PreparedPage
from extraction.pipeline.domain.quality import QualityScores


@dataclass(frozen=True, slots=True, kw_only=True)
class PrepareDocumentCommand:
    """The bytes of one uploaded file. The worker reads them from object storage before calling the service."""

    content: bytes


@dataclass(frozen=True, slots=True, kw_only=True)
class ExtractDocumentCommand:
    """Pages already prepared (PREPARE) with the quality measured on each. Standard templates read page 0."""

    pages: tuple[PreparedPage, ...]
    qualities: tuple[QualityScores, ...]
