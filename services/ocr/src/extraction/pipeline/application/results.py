"""Outputs of the pipeline use cases."""

from dataclasses import dataclass

from extraction.pipeline.domain.models import PreparedPage
from extraction.pipeline.domain.quality import QualityScores


@dataclass(frozen=True, slots=True, kw_only=True)
class PrepareDocumentResult:
    """Every page prepared for OCR, with the quality measured on each before preparation."""

    pages: tuple[PreparedPage, ...]
    qualities: tuple[QualityScores, ...]
