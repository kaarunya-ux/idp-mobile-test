"""CLASSIFY: which template does this page match (IDP-03 ch. 2.6, stage 4: keyword hits and aspect ratio)."""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from extraction.pipeline.domain.template import TemplateDefinition


@dataclass(frozen=True, slots=True, kw_only=True)
class Classification:
    """The template that matched and how many of its keywords were found (0 to 1)."""

    template: TemplateDefinition
    confidence: float


def classify_page(
    templates: Sequence[TemplateDefinition], *, page_text: str, width: int, height: int
) -> Classification | None:
    """The template with most keyword hits, at least its ``min_keyword_hits``.

    A page of the wrong shape halves the confidence instead of being refused: photos are often cropped (the sample
    Aadhaar strips are about 3:1) and the words say more than the shape does.

    ``None`` means unknown: the generic path (F-EX-12) is not built yet, so the caller reports it for review.
    """
    best: Classification | None = None
    best_hits = 0
    ratio = max(width, height) / min(width, height)
    for template in templates:
        shaped = abs(ratio - template.aspect_ratio) <= template.aspect_tolerance * template.aspect_ratio
        hits = sum(re.search(keyword, page_text, re.IGNORECASE) is not None for keyword in template.keywords)
        if hits >= template.min_keyword_hits and hits > best_hits:
            best_hits = hits
            best = Classification(
                template=template, confidence=hits / len(template.keywords) * (1.0 if shaped else 0.5)
            )
    return best
