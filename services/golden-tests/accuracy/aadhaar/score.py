"""Scores a card's answer from ``POST /api/v1/extract`` field by field. No network, no pictures: numbers in, words out.

outcome (what the value is):
  correct         the value is what was printed (case, spacing and punctuation ignored, dates in any format)
  wrong_accepted  a different value, and the service marked it ACCEPTED: nobody will look at it. The number that matters most
  wrong_review    a different value, but the service marked it NEEDS_REVIEW: a person will see it
  missing         no value came back (or no field, or the document was not recognised as an Aadhaar card)
exact           the value is the printed text character for character (the service returns values as printed)
reviewed        the status is NEEDS_REVIEW, whatever the value: this is what the review rate counts

The Aadhaar number is always returned masked ("XXXX XXXX 1234"). It is correct only in exactly that form with the last four
digits that were printed. Another form with the right last four digits is wrong: the number would be unmasked.
For the cards whose number has a wrong check digit the right outcome is the opposite: the number must NOT be accepted
(status NEEDS_REVIEW, still masked, or no value), and an ACCEPTED number is wrong_accepted.
"""

import re
from datetime import datetime

MASKED = re.compile(r"^X{4} X{4} [0-9]{4}$")
OUTCOMES = ("correct", "wrong_accepted", "wrong_review", "missing")


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
    reviewed = status == "NEEDS_REVIEW"
    if group == "wrong_checksum" and key == "aadhaar_number":
        right = reviewed and (value is None or bool(MASKED.match(re.sub(r"\s+", " ", value.strip().upper()))))
    else:
        right = value is not None and same_value(key, expected, value)
    if right:
        outcome = "correct"
    elif value is None:
        outcome = "missing"
    else:
        outcome = "wrong_review" if reviewed else "wrong_accepted"
    exact = value == expected
    return {"outcome": outcome, "exact": exact, "reviewed": reviewed, "value": value, "status": status, "source": field.get("source") if field else None}


def score_card(truth: dict[str, str], response: dict | None, group: str = "normal") -> dict[str, dict[str, object]]:
    """One entry per field of ``truth``. A missing answer or another document type leaves every field missing."""
    recognised = response is not None and response.get("documentType") == "aadhaar_card"
    by_key = {f["key"]: f for f in (response or {}).get("fields", [])} if recognised else {}
    return {key: score_field(key, expected, by_key.get(key), group) for key, expected in truth.items()}
