"""The two standard templates (scope STANDARD): PAN card and Aadhaar card, written as code until the template feature
stores definitions as JSON (IDP-03 ch. 2.5). Regions are page fractions measured on the sample cards in the repository.
"""

from extraction.pipeline.domain.template import (
    Accept,
    AcceptMode,
    Anchor,
    FieldDefinition,
    FieldType,
    Mask,
    RuleKind,
    RuleSpec,
    TemplateDefinition,
)
from extraction.rule import PAN_RULE_ID, Normalizer
from extraction.rule.aadhaar import AADHAAR_RULE_ID
from idp_ocr_engines.model import Box

_NAME_RULE = RuleSpec(id="name-letters", kind=RuleKind.REGEX, pattern=r"[A-Z][A-Z .'\-]{2,78}")
_NAME_NORMALIZE = (Normalizer.UPPER, Normalizer.COLLAPSE_SPACES)
_NAME_ACCEPT = Accept(mode=AcceptMode.SIMILARITY)
_PAN_CAPTIONS = r"(?i)name|income|govt|india|permanent|account|date|birth|signature"
_DATE_NORMALIZE = (Normalizer.STRIP_SPACES,)

PAN_CARD = TemplateDefinition(
    template_id="standard-pan-card",
    version=1,
    scope="STANDARD",
    document_type="PAN_CARD",
    aspect_ratio=1.586,
    aspect_tolerance=0.25,
    keywords=(r"INCOME\s*TAX", r"Permanent\s*Account", r"GOVT"),
    min_keyword_hits=1,
    anchors=(Anchor(id="title", text="INCOME TAX DEPARTMENT", region=Box(x=0.03, y=0.1, width=0.4, height=0.07)),),
    fields=(
        FieldDefinition(
            key="pan_number",
            label="PAN",
            type=FieldType.ID,
            box=Box(x=0.05, y=0.24, width=0.8, height=0.3),
            relative_to="title",
            extract=r"[A-Z0-9]{10}",
            rule=RuleSpec(id=PAN_RULE_ID, kind=RuleKind.CUSTOM, validator="PAN", pattern=r"[A-Z]{5}[0-9]{4}[A-Z]"),
            normalize=(Normalizer.UPPER, Normalizer.STRIP_SPACES, Normalizer.OCR_CONFUSABLES_ALNUM),
        ),
        FieldDefinition(
            key="name",
            label="Name",
            type=FieldType.NAME,
            box=Box(x=0.0, y=0.5, width=0.7, height=0.15),
            labels=_PAN_CAPTIONS,
            rule=_NAME_RULE,
            normalize=_NAME_NORMALIZE,
            accept=_NAME_ACCEPT,
        ),
        FieldDefinition(
            key="father_name",
            label="Father's name",
            type=FieldType.NAME,
            box=Box(x=0.0, y=0.63, width=0.7, height=0.17),
            labels=_PAN_CAPTIONS,
            rule=_NAME_RULE,
            normalize=_NAME_NORMALIZE,
            accept=_NAME_ACCEPT,
        ),
        FieldDefinition(
            key="dob",
            label="Date of birth",
            type=FieldType.DATE,
            box=Box(x=0.0, y=0.82, width=0.5, height=0.18),
            labels=_PAN_CAPTIONS,
            extract=r"[0-9]{2}\s?/\s?[0-9]{2}\s?/\s?[0-9]{4}",
            rule=RuleSpec(id="dob-date", kind=RuleKind.DATE, formats=("dd/MM/yyyy",)),
            normalize=_DATE_NORMALIZE,
        ),
    ),
)

_DIGITS_OR_LOOKALIKES = r"[0-9OSIlB]"
_AADHAAR_NUMBER = (
    rf"(?<![0-9]){_DIGITS_OR_LOOKALIKES}{{4}}\s?{_DIGITS_OR_LOOKALIKES}{{4}}\s?{_DIGITS_OR_LOOKALIKES}{{4}}(?![0-9])"
    r"|(?i:[x*]{4}\s?[x*]{4}\s?[0-9]{4})"
)
_AADHAAR_CAPTIONS = r"(?i)government|india|authority|unique|identification|aadhaar|dob|birth|male|issued|vid"
_WHOLE_PAGE = Box(x=0.0, y=0.0, width=1.0, height=1.0)
# OCR often reads the O of DOB as a zero; the Hindi caption "जन्म" (birth) also marks the line.
_DOB_LINE = r"(?i)\bd[o0]b\b|date\s*of\s*birth|year\s*of\s*birth|\by[o0]b\b|\u091c\u0928\u094d\u092e"
_DATE = r"[0-9]{2}\s?[/.\-]\s?[0-9]{2}\s?[/.\-]\s?[0-9]{4}"
# The value after a DOB or year-of-birth label. A bare date is not taken: a card also prints an issue date.
_AADHAAR_DOB = (
    rf"(?i)(?:\bd[o0]b\b|date\s*of\s*birth|birth)\s*[:/]?\s*({_DATE})"
    r"|(?i:(?:\by[o0]b\b|year\s*of\s*birth)\s*[:/]?\s*((?:19|20)[0-9]{2}))"
)
# From the "Address:" caption or a care-of marker (S/O, C/O, D/O, W/O) up to the PIN code that ends every address.
_AADHAAR_ADDRESS = r"(?i)(?:address\s*:?|\b[csdwh]\s*/\s*o\b\s*:?)\s*([A-Za-z0-9].{14,300}?[1-9][0-9]{5})(?![0-9])"

_DEVANAGARI = r"\u0900-\u097F"
# A piece with English letters and no Devanagari: keeps the English column and drops the Hindi one beside it.
# A lone number is left out because it may belong to the Hindi column.
_LATIN_ONLY = rf"^[^{_DEVANAGARI}]*[A-Za-z][^{_DEVANAGARI}]*$"
_HINDI_NAME_RULE = RuleSpec(id="name-hindi", kind=RuleKind.REGEX, pattern=rf"[{_DEVANAGARI}][{_DEVANAGARI} ]{{1,78}}")
# Only Devanagari words and a lone PIN code are kept, so the English column beside the Hindi one is left out.
_DEVANAGARI_OR_PIN = rf"[{_DEVANAGARI}]|^\W*[1-9][0-9]{{5}}\W*$"
# From the Hindi "address" caption to the PIN code that ends it.
_HINDI_ADDRESS = r"(?:\u092a\u0924\u094d\u0924\u093e|\u092a\u0924\u093e)\W*(.{10,300}?[1-9][0-9]{5})(?![0-9])"
_HINDI_ADDRESS_RULE = RuleSpec(
    id="address-hindi", kind=RuleKind.REGEX, pattern=rf"[{_DEVANAGARI}0-9][{_DEVANAGARI}0-9 ,.:;/()'\-]{{9,300}}"
)
_NAME_ACCEPT_ADDRESS = Accept(mode=AcceptMode.SIMILARITY)

AADHAAR_CARD = TemplateDefinition(
    template_id="standard-aadhaar-card",
    version=1,
    scope="STANDARD",
    document_type="AADHAAR_CARD",
    aspect_ratio=1.586,
    aspect_tolerance=0.45,
    keywords=(r"government\s*of\s*india", r"aadhaar", r"uidai", r"\bdob\b", r"[0-9]{4}\s[0-9]{4}\s[0-9]{4}"),
    min_keyword_hits=2,
    anchors=(),
    fields=(
        FieldDefinition(
            key="aadhaar_number",
            label="Aadhaar number",
            type=FieldType.ID,
            box=_WHOLE_PAGE,
            extract=_AADHAAR_NUMBER,
            rule=RuleSpec(id=AADHAAR_RULE_ID, kind=RuleKind.CUSTOM, validator="AADHAAR"),
            normalize=(Normalizer.UPPER, Normalizer.STRIP_SPACES, Normalizer.OCR_CONFUSABLES_NUMERIC),
            mask=Mask(keep_last=4),
        ),
        FieldDefinition(
            key="name",
            label="Name",
            type=FieldType.NAME,
            box=_WHOLE_PAGE,
            labels=_AADHAAR_CAPTIONS,
            keep=r"[A-Za-z]",
            above=_DOB_LINE,
            rule=_NAME_RULE,
            normalize=_NAME_NORMALIZE,
            accept=_NAME_ACCEPT,
        ),
        FieldDefinition(
            key="name_hi",
            label="Name (Hindi)",
            type=FieldType.NAME,
            box=_WHOLE_PAGE,
            above=_DOB_LINE,
            max_lines_above=2,
            keep=rf"[{_DEVANAGARI}]",
            language="hi",
            required=False,
            rule=_HINDI_NAME_RULE,
            normalize=(Normalizer.COLLAPSE_SPACES,),
            accept=_NAME_ACCEPT,
        ),
        FieldDefinition(
            key="dob",
            label="Date of birth or year",
            type=FieldType.DATE,
            box=_WHOLE_PAGE,
            extract=_AADHAAR_DOB,
            rule=RuleSpec(
                id="dob-date", kind=RuleKind.DATE, formats=("dd/MM/yyyy", "dd-MM-yyyy", "dd.MM.yyyy", "yyyy")
            ),
            normalize=_DATE_NORMALIZE,
        ),
        FieldDefinition(
            key="gender",
            label="Gender",
            type=FieldType.TEXT,
            box=_WHOLE_PAGE,
            extract=r"(?i)\b(?:female|male|transgender)\b",
            rule=RuleSpec(id="gender-enum", kind=RuleKind.REGEX, pattern=r"MALE|FEMALE|TRANSGENDER"),
            normalize=(Normalizer.UPPER,),
        ),
        FieldDefinition(
            key="pincode",
            label="PIN code",
            type=FieldType.NUMBER,
            box=_WHOLE_PAGE,
            required=False,
            extract=r"(?<![0-9])[1-9][0-9]{5}(?![0-9])",
            rule=RuleSpec(id="pincode-format", kind=RuleKind.REGEX, pattern=r"[1-9][0-9]{5}"),
            normalize=(Normalizer.STRIP_SPACES,),
        ),
        FieldDefinition(
            key="address",
            label="Address",
            type=FieldType.ADDRESS,
            box=_WHOLE_PAGE,
            required=False,
            extract=_AADHAAR_ADDRESS,
            labels=r"(?i)qr\s*code|photograph",
            keep=_LATIN_ONLY,
            starts_at=r"(?i)^\W*address\b",
            excludes=("aadhaar_number", "name", "dob", "gender"),
            rule=RuleSpec(id="address-text", kind=RuleKind.REGEX, pattern=r"[A-Z0-9][A-Z0-9 ,.:;/()'\-]{14,300}"),
            normalize=(Normalizer.UPPER, Normalizer.COLLAPSE_SPACES),
            accept=Accept(mode=AcceptMode.SIMILARITY),
        ),
        FieldDefinition(
            key="address_hi",
            label="Address (Hindi)",
            type=FieldType.ADDRESS,
            box=_WHOLE_PAGE,
            language="hi",
            required=False,
            keep=_DEVANAGARI_OR_PIN,
            starts_at=r"^\W*(?:\u092a\u0924\u094d\u0924\u093e|\u092a\u0924\u093e)",
            extract=_HINDI_ADDRESS,
            excludes=("name_hi",),
            rule=_HINDI_ADDRESS_RULE,
            normalize=(Normalizer.COLLAPSE_SPACES,),
            accept=_NAME_ACCEPT_ADDRESS,
        ),
    ),
)

STANDARD_TEMPLATES = (PAN_CARD, AADHAAR_CARD)
