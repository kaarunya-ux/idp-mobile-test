"""The 100 invented PAN cards of the accuracy set and the value each field should be read as, as printed.

Nothing here is a real person or a real PAN: names are built from made-up surnames, PAN numbers are random in the shape the service's
rule enforces, ``AAAHA9999A`` (three letters, a holder type from ``ABCFGHJLPT``, a letter, four digits, a letter). The rule checks the
shape only; it does not check that the fifth letter is the surname's initial. 98 cards carry a valid number; 2 carry a holder-type letter
the rule does not allow and are their own class: the right outcome is that the number is not accepted (NEEDS_REVIEW).
"""

import random
from dataclasses import dataclass
from itertools import combinations, cycle, islice

SEED = 20261112
FIELDS = ("pan_number", "name", "father_name", "dob")
FONTS = ("sans", "serif", "mono")
EFFECTS = ("angle", "strong_blur", "faded", "glare", "lowres", "crop", "table")  # the picture defects, English text only
CARDS = 100
WRONG_CARDS = 2
FIRST_NAMES = ("Anil", "Meera", "Rohit", "Sunita", "Karan", "Divya", "Clement", "Dilnaz", "Lalit", "Omkar", "Marnik", "Kamal", "Lalita", "Claribel")
SURNAMES = ("Testrao", "Samplekar", "Demoji", "Mockwal", "Fakepatil", "Clarkar", "Mornani", "Dolwal", "Lomhade", "Marnekar", "Olwani")
HOUSES = ("10", "1/10", "101", "Plot 10", "O-1", "Block O, 101", "Lane 11", "88")
AREAS = ("Sample Nagar", "Demo Colony", "Clarmor Layout", "Mock Enclave", "Old Cloth Market", "Lalmor Road", "Dollclark Park")
CITIES = (
    ("Testpur", "Karnataka"),
    ("Clarpur", "Karnataka"),
    ("Samplegarh", "Haryana"),
    ("Marnal", "Haryana"),
    ("Demopet", "Chandigarh"),
    ("Dolmahal", "Chandigarh"),
    ("Mockabad", "Maharashtra"),
)
SURNAMES = ("Testrao", "Samplekar", "Demoji", "Mockwal", "Fakepatil", "Clarkar", "Mornani", "Dolwal", "Lomhade", "Marnekar", "Olwani")
HOUSES = ("10", "1/10", "101", "Plot 10", "O-1", "Block O, 101", "Lane 11", "88")
AREAS = ("Sample Nagar", "Demo Colony", "Clarmor Layout", "Mock Enclave", "Old Cloth Market", "Lalmor Road", "Dollclark Park")
CITIES = (
    ("Testpur", "Karnataka"),
    ("Clarpur", "Karnataka"),
    ("Samplegarh", "Haryana"),
    ("Marnal", "Haryana"),
    ("Demopet", "Chandigarh"),
    ("Dolmahal", "Chandigarh"),
    ("Mockabad", "Maharashtra"),
)
LETTERS = "ABCDGILMOQSZ"  # the ones OCR mixes with a digit (O/0, I/1, S/5, B/8) are over-represented
DIGITS = "0158" * 2 + "234679"
HOLDER_TYPES = "ABCFGHJLPT"  # what the rule allows in position four
BAD_HOLDER_TYPES = "ZXKMNQ"  # what it does not


@dataclass(frozen=True)
class CardSpec:
    card_id: str
    group: str  # "normal" or "invalid_number" (the number has a holder-type letter the rule does not allow)
    font: str
    effects: tuple[str, ...]
    name: str
    father: str
    dob: str  # dd/mm/yyyy
    number: str

    @property
    def layout(self) -> str:
        return "single"

    def truth(self) -> dict[str, str]:
        return {"pan_number": self.number, "name": self.name.upper(), "father_name": self.father.upper(), "dob": self.dob}


def _plan() -> list[tuple[str, tuple[str, ...]]]:
    plan: list[tuple[str, tuple[str, ...]]] = [("sans", ())] * 4 + [("serif", ())] * 3 + [("mono", ())] * 3
    plan += [(FONTS[n % 3], (e,)) for e in EFFECTS for n in range(8)]
    plan += [(FONTS[n % 3], pair) for n, pair in enumerate(islice(cycle(combinations(EFFECTS, 2)), 32))]
    return plan + [("sans", ())] * WRONG_CARDS


def build_cards() -> list[CardSpec]:
    rng = random.Random(SEED)
    cards = []
    for index, (font_name, effects) in enumerate(_plan(), start=1):
        bad = index > CARDS - WRONG_CARDS
        surname = rng.choice(SURNAMES)
        holder = rng.choice(BAD_HOLDER_TYPES) if bad else "P"
        number = "".join(rng.choice(LETTERS) for _ in range(3)) + holder + surname[0].upper()
        number += "".join(rng.choice(DIGITS) for _ in range(4)) + rng.choice(LETTERS)
        cards.append(
            CardSpec(
                card_id=f"P{index:02d}",
                group="invalid_number" if bad else "normal",
                font=font_name,
                effects=effects,
                name=f"{rng.choice(FIRST_NAMES)} {surname}",
                father=f"{rng.choice(FIRST_NAMES)} {surname}",
                dob=f"{rng.randint(1, 28):02d}/{rng.randint(1, 12):02d}/{rng.randint(1950, 2004)}",
                number=number,
            )
        )
    return cards
