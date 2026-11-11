"""Scores a card's answer from ``POST /api/v1/extract`` field by field. No network, no pictures: numbers in, words out.

correct   the value is what was printed
wrong     a value was returned and it is not
empty     no value came back (or no field, or the document was not recognised as an Aadhaar card)
review    the service sent the field to a person (status NEEDS_REVIEW); ``review_right`` says whether its value was right

The Aadhaar number is always returned masked ("XXXX XXXX 1234"). It is correct only in exactly that form with the last four
digits that were printed. Another form with the right last four digits is wrong: the number would be unmasked.
For the cards whose number has a wrong check digit the right outcome is the opposite: the number must NOT be accepted
(status NEEDS_REVIEW, still masked, or no value), and an ACCEPTED number is wrong.
"""

import re
from datetime import datetime

MASKED = re.compile(r"^X{4} X{4} [0-9]{4}$")
OUTCOMES = ("correct", "wrong", "empty", "review")


def _squash(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", value.upper())


def _date(value: str) -> str:
    for pattern in ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.strip(), pattern).strftime("%d%m%Y")
        except ValueError:
            continue
    return _squash(value)


def same_value(key: str, expected: str, value: str) -> bool:
    if key == "aadhaar_number":
        return re.sub(r"\s+", " ", value.strip().upper()) == expected
    if key == "dob":
        return _date(value) == _date(expected)
    return _squash(value) == _squash(expected)


def score_field(key: str, expected: str, field: dict | None, group: str = "normal") -> dict[str, object]:
    value = field.get("value") if field else None
    value = value if isinstance(value, str) and value.strip() else None
    status = field.get("status") if field else None
    source = field.get("source") if field else None
    if group == "wrong_checksum" and key == "aadhaar_number":
        masked = value is None or bool(MASKED.match(re.sub(r"\s+", " ", value.strip().upper())))
        return {"outcome": "correct" if status == "NEEDS_REVIEW" and masked else "wrong", "value": value, "status": status, "source": source}
    right = value is not None and same_value(key, expected, value)
    if status == "NEEDS_REVIEW":
        return {"outcome": "review", "review_right": right, "value": value, "status": status, "source": source}
    if value is None:
        return {"outcome": "empty", "value": None, "status": status}
    return {"outcome": "correct" if right else "wrong", "value": value, "status": status, "source": source}


def score_card(truth: dict[str, str], response: dict | None, group: str = "normal") -> dict[str, dict[str, object]]:
    """One entry per field of ``truth``. A missing answer or another document type leaves every field empty."""
    recognised = response is not None and response.get("documentType") == "aadhaar_card"
    by_key = {f["key"]: f for f in (response or {}).get("fields", [])} if recognised else {}
    return {key: score_field(key, expected, by_key.get(key), group) for key, expected in truth.items()}
