"""Prompts and answer schemas for reading Indian ID cards with a vision model (from the web extraction app).

Ported from ``idp_web_app/doc_schemas.py`` (feature/visiting_card_extraction, Prakyath), which was benchmarked on
real cards. The model answers with a fixed-order JSON array, not an object: key names aren't generated, which cuts
output tokens by about 40%. ``FIELD_KEYS`` maps each answer position to the template field it fills; ``None``
means the value is read but no template field takes it yet.

Used only by the local backup ``VisionModelFieldReader`` (ADR 0006). ai-gateway has its own copy with its versioned
prompt files (IDP-03 ch. 4.4, LLM-02); ``PROMPT_VERSION`` names this set.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

PROMPT_VERSION = "card-fields.v1"

_STR: dict[str, Any] = {"type": ["string", "null"], "maxLength": 120}
_DATE: dict[str, Any] = {"type": ["string", "null"], "maxLength": 20}
_ADDRESS: dict[str, Any] = {"type": ["string", "null"], "maxLength": 400}
_RULES = (
    "Copy every value exactly as printed in English, character by character. "
    "Use null for a field that is not printed or not readable. Never guess or invent a value."
)


def _pattern(regex: str) -> dict[str, Any]:
    return {"anyOf": [{"type": "string", "pattern": regex}, {"type": "null"}]}


@dataclass(frozen=True, slots=True, kw_only=True)
class CardProfile:
    """One document type: what to ask, the shape of each answer, and which template field it fills."""

    prompt: str
    fields: Mapping[str, dict[str, Any]]  # answer name -> JSON schema of the value
    field_keys: Mapping[str, str | None]  # answer name -> template field key
    values: Mapping[str, Mapping[str, str]]  # answer name -> how a printed code becomes the template's value

    @property
    def schema(self) -> dict[str, Any]:
        items = list(self.fields.values())
        return {"type": "array", "prefixItems": items, "minItems": len(items), "maxItems": len(items)}

    @property
    def full_prompt(self) -> str:
        return (
            f"{self.prompt}{_RULES} Answer with a JSON array of exactly {len(self.fields)} values in this order: "
            + ", ".join(self.fields)
            + "."
        )


PAN = CardProfile(
    prompt=(
        "This is an Indian PAN card. Read these fields:\n"
        '- pan_number: the 10-character code under "Permanent Account Number" (5 letters, 4 digits, 1 letter).\n'
        '- name: the card holder\'s name (under "Name", or the first name line on older cards).\n'
        '- parent_name: the name under "Father\'s Name" or "Mother\'s Name" (not the label itself).\n'
        '- parent_relation: "father" or "mother", from that label. On older cards without a label, '
        "the second name line is the father's name.\n"
        "- dob: the date of birth, DD/MM/YYYY.\n"
    ),
    fields={
        "pan_number": _pattern(r"^[A-Z]{5}[0-9]{4}[A-Z]$"),
        "name": _STR,
        "parent_name": _STR,
        "parent_relation": {"enum": ["father", "mother", None]},
        "dob": _pattern(r"^[0-3][0-9]/[01][0-9]/[12][0-9]{3}$"),
    },
    field_keys={
        "pan_number": "pan_number",
        "name": "name",
        "parent_name": "father_name",  # the standard template's name for it
        "parent_relation": None,
        "dob": "dob",
    },
    values={},
)

AADHAAR = CardProfile(
    prompt=(
        "This is an Indian Aadhaar card or letter. It may show the front, the back, or both. Read these fields:\n"
        "- aadhaar_number: the 12-digit number printed in three groups of four (e.g. 1234 5678 9012). "
        "Not the 16-digit VID and not the enrolment number.\n"
        "- name: the holder's name in English (never the regional-language line).\n"
        "- dob: the date of birth as DD/MM/YYYY, or only the year if just the year of birth is printed.\n"
        "- gender: M, F or O.\n"
        "- address: the complete English address block, every line from the first to the PIN code, joined "
        'into one line with spaces, keeping the printed punctuation. Leave out the "Address:" label, the '
        "holder's own name and any mobile number. null if no address is printed.\n"
        "- pincode: the 6-digit PIN code at the end of the address.\n"
    ),
    fields={
        "aadhaar_number": _pattern(r"^[0-9X]{4} [0-9X]{4} [0-9]{4}$"),  # masked copies print XXXX XXXX 1234
        "name": _STR,
        "dob": _pattern(r"^([0-3][0-9]/[01][0-9]/)?[12][0-9]{3}$"),  # older cards print only the year
        "gender": {"enum": ["M", "F", "O", None]},
        "address": _ADDRESS,
        "pincode": _pattern(r"^[1-9][0-9]{5}$"),
    },
    field_keys={
        "aadhaar_number": "aadhaar_number",
        "name": "name",
        "dob": "dob",
        "gender": "gender",
        "address": "address",
        "pincode": "pincode",
    },
    values={"gender": {"M": "MALE", "F": "FEMALE", "O": "TRANSGENDER"}},  # the template's gender-enum rule
)

DRIVING_LICENCE = CardProfile(
    prompt=(
        "This is an Indian driving licence. It may show the front, the back, or both. Read these fields:\n"
        "- license_number: the licence number (labelled DL No, Licence No or similar), "
        "with its spaces and dashes exactly as printed.\n"
        "- name: the holder's name.\n"
        "- dob: date of birth.\n"
        "- address: the complete address, every line joined into one line with spaces, keeping the printed "
        "punctuation, including the PIN code.\n"
        "- issue_date: date of issue.\n"
        "- validity_nt: validity for non-transport vehicles (Validity NT, or the only validity date if just one "
        "is printed).\n"
        "- validity_tr: validity for transport vehicles (Validity TR); null if not printed.\n"
        "- blood_group: as printed, e.g. B+; null if not printed or marked unknown.\n"
        "- organ_donor: Y or N, from the Organ Donor mark; null if not printed.\n"
        "- vehicle_classes: the vehicle class codes authorised (e.g. MCWG, LMV), comma-separated. "
        "Read the codes in the table rows, not the column headers.\n"
        "Keep every date in the printed format. "
    ),
    fields={
        "license_number": _STR,
        "name": _STR,
        "dob": _DATE,
        "address": _ADDRESS,
        "issue_date": _DATE,
        "validity_nt": _DATE,
        "validity_tr": _DATE,
        "blood_group": {"enum": ["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-", None]},
        "organ_donor": {"enum": ["Y", "N", None]},
        "vehicle_classes": _STR,
    },
    # No standard DL template yet (build step E3): the keys are the ones it will use.
    field_keys={key: key for key in ("license_number", "name", "dob", "address", "issue_date")}
    | {key: key for key in ("validity_nt", "validity_tr", "blood_group", "organ_donor", "vehicle_classes")},
    values={},
)

PROFILES: Mapping[str, CardProfile] = {
    "PAN_CARD": PAN,
    "AADHAAR_CARD": AADHAAR,
    "DRIVING_LICENCE": DRIVING_LICENCE,
}
