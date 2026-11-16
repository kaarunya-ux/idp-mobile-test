"""Finds the template an uploaded document is, for ``/extract/auto`` (ADR 0016): keywords first, then the AI.

Not a service of its own: ``QuickExtractService`` uses it, then extracts with what it found.
"""

from collections.abc import Callable, Sequence

from extraction.pipeline.domain.exceptions import AiUnavailableError
from extraction.quick.domain.detection import (
    Candidate,
    Match,
    UserTemplate,
    by_code,
    by_keywords,
    ours,
    read_enough,
    scored,
    shortlist,
    users,
)
from extraction.quick.domain.models import QuickCaller, QuickResult, StagedFile
from extraction.quick.domain.ports import TemplateClassifierPort, TemplateListPort
from idp_ocr_engines.model import OcrPage


class TemplateMatcher:
    def __init__(
        self,
        *,
        templates: TemplateListPort,
        classifier: TemplateClassifierPort | None,
        minimum: float,
        min_filled: float,
    ) -> None:
        self._templates = templates
        self._classifier = classifier
        self._minimum = minimum  # QUICK__DETECT_MIN_CONFIDENCE
        self._min_filled = min_filled  # QUICK__DETECT_MIN_FILLED

    def confirmed(self, result: QuickResult) -> bool:
        """Whether the found template read enough of its fields to keep; else the page goes to the generic read."""
        return read_enough(result, self._min_filled)

    def candidates(self, caller: QuickCaller, sent: Sequence[UserTemplate]) -> list[Candidate]:
        """Ours (standard and the account's own) and the user's that the backend sent."""
        return [ours(t) for t in self._templates.templates_for(caller)] + [users(t) for t in sent]

    def match(
        self,
        caller: QuickCaller,
        candidates: Sequence[Candidate],
        pages: Sequence[OcrPage],
        first_page: Callable[[], StagedFile],
    ) -> Match | None:
        """The candidate by its keywords; else the one the AI names among the best-scoring ones; else None.
        ``first_page`` stores the first page for the AI, only when it is asked."""
        ranked = scored(candidates, pages)
        found = by_keywords(ranked, self._minimum)
        codes = shortlist(ranked)  # those with some evidence on the page, as document type codes
        if found is not None or self._classifier is None or not codes:
            return found  # no template shows any sign of being this document: generic, no AI call
        try:
            code, confidence = self._classifier.classify(caller, first_page(), list(codes))
        except AiUnavailableError:
            return None  # the keywords found nothing and the model can't say: generic
        return by_code(codes, code, confidence, self._minimum)
