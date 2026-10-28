"""Fields, prompts and JSON schemas for the three Indian documents: PAN, Aadhaar, driving licence.

Each type has: FIELDS (output keys, in order), a JSON schema (turned into a grammar by llama-server,
so the model can only emit well-formed values where the format is fixed) and a prompt. The caller says
which document it is (one endpoint per type), so there is no classify step.

The model answers with a fixed-order array of values, not an object: key names are not generated, which
cuts output tokens by ~40% (writing the answer is the largest part of the request). The app turns the
array back into the keyed response.
"""

DOC_TYPES = ("pan", "aadhaar", "dl")

_STR = {"type": ["string", "null"], "maxLength": 120}
_DATE = {"type": ["string", "null"], "maxLength": 20}


def _pattern(regex: str) -> dict:
    return {"anyOf": [{"type": "string", "pattern": regex}, {"type": "null"}]}


FIELD_SCHEMAS = {
    "pan": {
        "pan_number": _pattern(r"^[A-Z]{5}[0-9]{4}[A-Z]$"),
        "name": _STR,
        "parent_name": _STR,
        "parent_relation": {"enum": ["father", "mother", None]},
        "dob": _pattern(r"^[0-3][0-9]/[01][0-9]/[12][0-9]{3}$"),
    },
    "aadhaar": {
        # Masked copies print XXXX XXXX 1234; keep the X's as printed.
        "aadhaar_number": _pattern(r"^[0-9X]{4} [0-9X]{4} [0-9]{4}$"),
        "name": _STR,
        # Older cards print only the year of birth.
        "dob": _pattern(r"^([0-3][0-9]/[01][0-9]/)?[12][0-9]{3}$"),
        "gender": {"enum": ["M", "F", "O", None]},
        "address": {"type": ["string", "null"], "maxLength": 400},
        "pincode": _pattern(r"^[1-9][0-9]{5}$"),
    },
    "dl": {
        "license_number": _STR,
        "name": _STR,
        "dob": _DATE,
        "address": {"type": ["string", "null"], "maxLength": 400},
        "issue_date": _DATE,
        "validity_nt": _DATE,
        "validity_tr": _DATE,
        "blood_group": {"enum": ["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-", None]},
        "organ_donor": {"enum": ["Y", "N", None]},
        "vehicle_classes": _STR,
    },
}

FIELDS = {t: tuple(f) for t, f in FIELD_SCHEMAS.items()}

SCHEMAS = {
    t: {"type": "array", "prefixItems": list(props.values()), "minItems": len(props), "maxItems": len(props)}
    for t, props in FIELD_SCHEMAS.items()
}

_RULES = ("Copy every value exactly as printed in English, character by character. "
          "Use null for a field that is not printed or not readable. Never guess or invent a value.")

PROMPTS = {
    "pan": (
        "This is an Indian PAN card. Read these fields:\n"
        "- pan_number: the 10-character code under \"Permanent Account Number\" (5 letters, 4 digits, 1 letter).\n"
        "- name: the card holder's name (under \"Name\", or the first name line on older cards).\n"
        "- parent_name: the name under \"Father's Name\" or \"Mother's Name\" (not the label itself).\n"
        "- parent_relation: \"father\" or \"mother\", from that label. On older cards without a label, "
        "the second name line is the father's name.\n"
        "- dob: the date of birth, DD/MM/YYYY.\n" + _RULES),
    "aadhaar": (
        "This is an Indian Aadhaar card or letter. It may show the front, the back, or both. Read these fields:\n"
        "- aadhaar_number: the 12-digit number printed in three groups of four (e.g. 1234 5678 9012). "
        "Not the 16-digit VID and not the enrolment number.\n"
        "- name: the holder's name in English (never the regional-language line).\n"
        "- dob: the date of birth as DD/MM/YYYY, or only the year if just the year of birth is printed.\n"
        "- gender: M, F or O.\n"
        "- address: the complete English address block, every line from the first to the PIN code, joined "
        "into one line with spaces, keeping the printed punctuation. Leave out the \"Address:\" label, the "
        "holder's own name and any mobile number. null if no address is printed.\n"
        "- pincode: the 6-digit PIN code at the end of the address.\n" + _RULES),
    "dl": (
        "This is an Indian driving licence. It may show the front, the back, or both. Read these fields:\n"
        "- license_number: the licence number (labelled DL No, Licence No or similar), "
        "with its spaces and dashes exactly as printed.\n"
        "- name: the holder's name.\n"
        "- dob: date of birth.\n"
        "- address: the complete address, every line joined into one line with spaces, keeping the printed "
        "punctuation, including the PIN code.\n"
        "- issue_date: date of issue.\n"
        "- validity_nt: validity for non-transport vehicles (Validity NT, or the only validity date if just one is printed).\n"
        "- validity_tr: validity for transport vehicles (Validity TR); null if not printed.\n"
        "- blood_group: as printed, e.g. B+; null if not printed or marked unknown.\n"
        "- organ_donor: Y or N, from the Organ Donor mark; null if not printed.\n"
        "- vehicle_classes: the vehicle class codes authorised (e.g. MCWG, LMV), comma-separated. "
        "Read the codes in the table rows, not the column headers.\n"
        "Keep every date in the printed format. " + _RULES),
}


# The answer format, appended to every prompt.
for _t in PROMPTS:
    PROMPTS[_t] += (f" Answer with a JSON array of exactly {len(FIELDS[_t])} values in this order: "
                    + ", ".join(FIELDS[_t]) + ".")
