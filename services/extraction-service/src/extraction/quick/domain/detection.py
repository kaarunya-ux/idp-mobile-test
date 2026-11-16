"""Which template an uploaded document is (ADR 0016): ours, or one of the user's that the backend sent.

The candidates are the published templates the account may use (standard and its own, from our database) and the
templates the backend sends for this user (its RBAC decides which), built as custom templates (ADR 0013) whose
keywords are their labels. Each candidate is scored on each uploaded page: the share of its keywords found in the OCR
text, halved for a page of the wrong shape (ours only: a user's template has no shape). The best score of at least the
threshold wins, the user's own template on a tie. Otherwise the AI may name one, at least as sure.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum

from extraction.pipeline.domain.classify import classify_page
from extraction.pipeline.domain.custom_template import CustomField, custom_template, label_words
from extraction.pipeline.domain.template import TemplateDefinition
from extraction.quick.domain.models import QuickResult
from idp_ocr_engines.model import OcrPage

_SEPARATORS = r"[\s.:/_\-'\u2019]*"  # spaces, dots, colons, slashes, dashes, apostrophes
AI_CANDIDATES = 50  # ai-gateway's classify takes at most 50 document types


class DetectedBy(StrEnum):
    KEYWORDS = "KEYWORDS"
    AI = "AI"


@dataclass(frozen=True, slots=True, kw_only=True)
class UserTemplate:
    """A template the backend sent for this user: its id and name, and its keys as on the custom route."""

    id: str
    name: str
    fields: tuple[CustomField, ...]
    field_ids: Mapping[str, str] = field(default_factory=dict)  # key -> the id the backend sent with it


@dataclass(frozen=True, slots=True, kw_only=True)
class Candidate:
    id: str
    name: str
    template: TemplateDefinition
    own: bool  # the user's, sent by the backend; else ours
    field_ids: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True, kw_only=True)
class Match:
    candidate: Candidate
    confidence: float  # 0 to 1
    detected_by: DetectedBy


@dataclass(frozen=True, slots=True, kw_only=True)
class AutoResult:
    """``/extract/auto``'s answer: the document read, and the template it was read with (None: the generic read)."""

    result: QuickResult
    match: Match | None = None


def type_code(name: str) -> str:
    """A template's name as a document type code: "Green Card" -> "green_card"."""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "custom"


def ours(template: TemplateDefinition) -> Candidate:
    name = template.name or template.document_type.replace("_", " ").capitalize()
    return Candidate(id=template.template_id, name=name, template=template, own=False)


def users(found: UserTemplate) -> Candidate:
    """The user's template as a custom template whose keywords are its labels."""
    keywords = tuple(label_keyword(f.label) for f in found.fields)
    template = replace(custom_template(found.fields), template_id=found.id, keywords=keywords, min_keyword_hits=1)
    return Candidate(id=found.id, name=found.name, template=template, own=True, field_ids=found.field_ids)


def label_keyword(label: str) -> str:
    """A label as a keyword: its words in order, case and the separators between them aside, as whole words."""
    return r"(?<![^\W_])" + _SEPARATORS.join(re.escape(word) for word in label_words(label)) + r"(?![^\W_])"


def scored(candidates: Sequence[Candidate], pages: Sequence[OcrPage]) -> list[tuple[float, Candidate]]:
    """Each candidate with its best score on any page (0 when too few of its keywords are found), best first; the
    user's own before ours on a tie."""
    scores = []
    for candidate in candidates:
        found = [
            classify_page([candidate.template], page_text=page.text, width=page.width, height=page.height)
            for page in pages
            if page.words
        ]
        scores.append((max((f.confidence for f in found if f is not None), default=0.0), candidate))
    return sorted(scores, key=lambda pair: (pair[0], pair[1].own), reverse=True)


def by_keywords(ranked: Sequence[tuple[float, Candidate]], minimum: float) -> Match | None:
    """The best-scoring candidate, when its score is at least ``minimum``."""
    if not ranked or ranked[0][0] < minimum:
        return None
    score, candidate = ranked[0]
    return Match(candidate=candidate, confidence=round(score, 3), detected_by=DetectedBy.KEYWORDS)


def shortlist(ranked: Sequence[tuple[float, Candidate]]) -> dict[str, Candidate]:
    """The candidates the AI may choose from: only those with evidence on the page (at least their minimum of
    keywords found, so a score above 0), best first, ``AI_CANDIDATES`` at most, each under a document type code
    (``AADHAAR_CARD``; a user's from its name, ``JAMAICA_NATIONAL_ID``), unique. A small model asked to pick among
    templates the page shows no sign of picks one anyway (a Jamaican ID was named an Aadhaar card, 90% sure): the
    model confirms, it never guesses."""
    codes: dict[str, Candidate] = {}
    for _, candidate in [pair for pair in ranked if pair[0] > 0][:AI_CANDIDATES]:
        base = (candidate.template.document_type if not candidate.own else type_code(candidate.name)).upper()[:60]
        code, number = base, 2
        while code in codes:
            code, number = f"{base}_{number}", number + 1
        codes[code] = candidate
    return codes


def read_enough(result: QuickResult, minimum: float) -> bool:
    """Whether the template read at least ``minimum`` of its fields (a value that isn't empty). A template the page
    isn't (the AI named PAN for a page whose only sign of it was "GOVT") reads next to nothing: that page goes to the
    generic read instead."""
    if not result.fields:
        return False
    filled = sum(1 for f in result.fields if f.value is not None and f.value != "")
    return filled / len(result.fields) >= minimum


def by_code(codes: Mapping[str, Candidate], code: str | None, confidence: float, minimum: float) -> Match | None:
    """The candidate the AI named (by its code), when the AI is at least ``minimum`` sure."""
    chosen = codes.get(code) if code is not None else None
    if chosen is None or confidence < minimum:
        return None
    return Match(candidate=chosen, confidence=round(confidence, 3), detected_by=DetectedBy.AI)
