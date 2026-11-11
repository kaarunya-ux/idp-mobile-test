"""A custom template: the keys a user defined for a document we have no template for (ADR 0013).

The backend sends the keys with each request (``key``, the label printed on the document, a type, and the box the
user drew around the value on the sample, if any); this builds a template from them for that one request, and nothing
is stored. A key with a box is read inside it (``FieldDefinition.clip_to_box``); one without, or whose box holds
nothing, is read by its label, wherever it is on the page (``FieldDefinition.value_after``): the text after the label
on its line up to a column gap, or the line below. Both OCR engines must agree, as for any field; what they don't
settle goes to the AI with the labels. Every key is required, so a key OCR can't find is asked of the AI rather than
left empty.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
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
from extraction.rule import PRINTED_DATE
from extraction.rule.normalizers import Normalizer
from idp_ocr_engines.model import Box

CUSTOM_SCOPE = "CUSTOM"
CUSTOM_DOCUMENT_TYPE = "CUSTOM"
MAX_FIELDS = 50  # ai-gateway reads at most 50 fields in one call

_WHOLE_PAGE = Box(x=0.0, y=0.0, width=1.0, height=1.0)
_DRAWN_MARGIN_X = 0.005  # page fraction added left and right of a drawn box: a hand-drawn edge is not exact
_DRAWN_MARGIN_Y = 0.01  # and above and below it: a value's line is often drawn a little high or low
_SEPARATORS = r"[\s.:/_\-'\u2019]*"  # between a label's words: spaces, dots, colons, slashes, dashes, apostrophes
_DATE = f"({PRINTED_DATE})"  # any printed form: the value is returned as printed, so day or month first is no matter
_LABEL_END = re.compile(r"[#№:.]+$")  # punctuation that ends a label as typed ("USCIS#", "No.") is part of it
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
    box: Box | None = None  # drawn around the value on the sample's first page; None: found by its label


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
    end = _LABEL_END.search(label.strip())
    after = f"(?:{re.escape(end.group())})?" if end else ""
    return r"(?i)^\W*" + _SEPARATORS.join(re.escape(word) for word in words) + r"(?![^\W_])" + after


def _definition(found: CustomField) -> FieldDefinition:
    text = FieldDefinition(
        key=found.key,
        label=found.label,
        type=FieldType.TEXT,
        box=_WHOLE_PAGE if found.box is None else _with_margin(found.box),
        value_after=label_pattern(found.label),
        clip_to_box=found.box is not None,
        rule=RuleSpec(id="custom-text", kind=RuleKind.REGEX, pattern=r".{1,400}"),
        normalize=(Normalizer.COLLAPSE_SPACES,),
        accept=Accept(mode=AcceptMode.SIMILARITY),
    )
    if found.type is CustomType.DATE:
        date_rule = RuleSpec(id="custom-date", kind=RuleKind.CUSTOM, validator="DATE_AS_PRINTED")
        return replace(
            text,
            type=FieldType.DATE,
            extract=_DATE,
            rule=date_rule,
            normalize=(Normalizer.COLLAPSE_SPACES,),  # "20 OCT 2002" keeps its spaces, as printed
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


def _with_margin(box: Box) -> Box:
    left, top = max(0.0, box.x - _DRAWN_MARGIN_X), max(0.0, box.y - _DRAWN_MARGIN_Y)
    right = min(1.0, box.x + box.width + _DRAWN_MARGIN_X)
    bottom = min(1.0, box.y + box.height + _DRAWN_MARGIN_Y)
    return Box(x=left, y=top, width=right - left, height=bottom - top)
