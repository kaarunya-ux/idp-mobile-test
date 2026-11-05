"""Hindi fields for the tests of reading a field in another language (the mechanism organisations' own templates can
use). The standard Aadhaar template no longer has them: the product reads English only for now. Made-up values."""

from dataclasses import replace

from extraction.pipeline.domain.standard_templates import AADHAAR_CARD
from extraction.pipeline.domain.template import (
    Accept,
    AcceptMode,
    FieldDefinition,
    FieldType,
    Normalizer,
    RuleKind,
    RuleSpec,
)
from idp_ocr_engines.model import Box

_DEVANAGARI = r"ऀ-ॿ"
_WHOLE_PAGE = Box(x=0.0, y=0.0, width=1.0, height=1.0)
_DOB_LINE = r"(?i)\bd[o0]b\b|date\s*of\s*birth|year\s*of\s*birth|\by[o0]b\b|जन्म"

NAME_HI = FieldDefinition(
    key="name_hi",
    label="Name (Hindi)",
    type=FieldType.NAME,
    box=_WHOLE_PAGE,
    above=_DOB_LINE,
    max_lines_above=2,
    keep=rf"[{_DEVANAGARI}]",
    language="hi",
    required=False,
    rule=RuleSpec(id="name-hindi", kind=RuleKind.REGEX, pattern=rf"[{_DEVANAGARI}][{_DEVANAGARI} ]{{1,78}}"),
    normalize=(Normalizer.COLLAPSE_SPACES,),
    accept=Accept(mode=AcceptMode.SIMILARITY),
)
ADDRESS_HI = FieldDefinition(
    key="address_hi",
    label="Address (Hindi)",
    type=FieldType.ADDRESS,
    box=_WHOLE_PAGE,
    language="hi",
    required=False,
    keep=rf"[{_DEVANAGARI}]|^\W*[1-9][0-9]{{5}}\W*$",
    starts_at=r"^\W*(?:पत्ता|पता)",
    extract=r"(?:पत्ता|पता)\W*(.{10,300}?[1-9][0-9]{5})(?![0-9])",
    excludes=("name_hi",),
    rule=RuleSpec(
        id="address-hindi", kind=RuleKind.REGEX, pattern=rf"[{_DEVANAGARI}0-9][{_DEVANAGARI}0-9 ,.:;/()'\-]{{9,300}}"
    ),
    normalize=(Normalizer.COLLAPSE_SPACES,),
    accept=Accept(mode=AcceptMode.SIMILARITY),
)
AADHAAR_WITH_HINDI = replace(AADHAAR_CARD, fields=(*AADHAAR_CARD.fields, NAME_HI, ADDRESS_HI))
