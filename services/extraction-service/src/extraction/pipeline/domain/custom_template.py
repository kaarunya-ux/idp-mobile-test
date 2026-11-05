"""A custom template: the keys a user defined for a document we have no template for (ADR 0013).

The backend sends the keys with each request (``key``, the label printed on the document, and a type); this builds a
template from them for that one request, and nothing is stored. Each field is read by its label, wherever it is on
the page (``FieldDefinition.value_after``): the text after the label on its line, or the line below. Both OCR engines
must agree, as for any field; what they don't settle goes to the AI with the labels. Every key is required, so a key
OCR can't find is asked of the AI rather than left empty.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date
from enum import StrEnum

from extraction.pipeline.domain.template import (
    Accept,
    AcceptMode,
    FieldDefinition,
    FieldType,
    RuleKind,
    RuleSpec,
    TemplateDefinition,
)
from extraction.rule.normalizers import Normalizer
from idp_ocr_engines.model import Box

CUSTOM_SCOPE = "CUSTOM"
CUSTOM_DOCUMENT_TYPE = "CUSTOM"
MAX_FIELDS = 50  # ai-gateway reads at most 50 fields in one call

_WHOLE_PAGE = Box(x=0.0, y=0.0, width=1.0, height=1.0)
_SEPARATORS = r"[\s.:/_\-'\u2019]*"  # between a label's words: spaces, dots, colons, slashes, dashes, apostrophes
_LATEST_DATE = date(2100, 12, 31)  # a custom date may be an expiry date, ahead of today
_DATE = r"([0-9]{1,2}\s?[/.\-]\s?[0-9]{1,2}\s?[/.\-]\s?[0-9]{4}|[0-9]{4}-[0-9]{2}-[0-9]{2})"
_NUMBER = r"([0-9][0-9 ,./\-]*[0-9]|[0-9])"


class CustomType(StrEnum):
    TEXT = "text"
    DATE = "date"
    NUMBER = "number"


@dataclass(frozen=True, slots=True, kw_only=True)
class CustomField:
    key: str  # lower snake_case, unique in the request
    label: str  # as printed on the document, as the user typed it
    type: CustomType = CustomType.TEXT


def custom_template(fields: Sequence[CustomField]) -> TemplateDefinition:
    """The template for one request's keys, in their order."""
    return TemplateDefinition(
        template_id="custom",
        version=1,
        scope=CUSTOM_SCOPE,
        document_type=CUSTOM_DOCUMENT_TYPE,
        aspect_ratio=1.0,
        aspect_tolerance=100.0,  # never classified: the request names it
        keywords=(),
        min_keyword_hits=0,
        anchors=(),
        fields=tuple(_definition(found) for found in fields),
    )


def label_pattern(label: str) -> str:
    """The label as printed, letter case and the separators between its words aside ("Date of birth:"). It must open
    its piece of text, as printed labels do: "Name" is not found inside "Father's Name", nor "Department" at the end
    of the title "INCOME TAX DEPARTMENT"."""
    words = re.findall(r"[^\W_]+", label)
    return r"(?i)^\W*" + _SEPARATORS.join(re.escape(word) for word in words) + r"(?![^\W_])"


def _definition(found: CustomField) -> FieldDefinition:
    text = FieldDefinition(
        key=found.key,
        label=found.label,
        type=FieldType.TEXT,
        box=_WHOLE_PAGE,
        value_after=label_pattern(found.label),
        rule=RuleSpec(id="custom-text", kind=RuleKind.REGEX, pattern=r".{1,400}"),
        normalize=(Normalizer.COLLAPSE_SPACES,),
        accept=Accept(mode=AcceptMode.SIMILARITY),
    )
    if found.type is CustomType.DATE:
        date_rule = RuleSpec(
            id="custom-date",
            kind=RuleKind.DATE,
            formats=("dd/MM/yyyy", "dd-MM-yyyy", "dd.MM.yyyy", "yyyy-MM-dd"),
            latest=_LATEST_DATE,
        )
        return replace(
            text,
            type=FieldType.DATE,
            extract=_DATE,
            rule=date_rule,
            normalize=(Normalizer.STRIP_SPACES,),
            accept=Accept(),
        )
    if found.type is CustomType.NUMBER:
        number_rule = RuleSpec(id="custom-number", kind=RuleKind.REGEX, pattern=r"[0-9][0-9,./\-]*")
        return replace(
            text,
            type=FieldType.NUMBER,
            extract=_NUMBER,
            rule=number_rule,
            normalize=(Normalizer.STRIP_SPACES,),
            accept=Accept(),
        )
    return text
