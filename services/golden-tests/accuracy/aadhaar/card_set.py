"""The 100 invented Aadhaar cards of the accuracy set and the value each field should be read as, exactly as printed.

Nothing here is a real person or a real number: names are built from made-up surnames, numbers are random 12-digit
strings given a Verhoeff check digit. Names and addresses are rich in letters OCR confuses (rn/m, cl/d, l/1, O/0).
98 cards carry a valid number and are scored for accuracy; 2 carry a number with a wrong check digit
and are their own class: the right outcome for them is that the number is not accepted.
"""

import random
from dataclasses import dataclass
from itertools import combinations, cycle, islice

SEED = 20261111
FIELDS = ("aadhaar_number", "name", "dob", "gender", "pincode", "address")
FONTS = ("sans", "serif", "mono")
EFFECTS = ("angle", "strong_blur", "faded", "glare", "lowres", "crop", "table")  # the picture defects, English text only
CARDS = 100
WRONG_CHECKSUM_CARDS = 2

# Verhoeff: the check digit scheme of the Aadhaar number (tables from the published algorithm).
_D = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 2, 3, 4, 0, 6, 7, 8, 9, 5),
    (2, 3, 4, 0, 1, 7, 8, 9, 5, 6),
    (3, 4, 0, 1, 2, 8, 9, 5, 6, 7),
    (4, 0, 1, 2, 3, 9, 5, 6, 7, 8),
    (5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
    (6, 5, 9, 8, 7, 1, 0, 4, 3, 2),
    (7, 6, 5, 9, 8, 2, 1, 0, 4, 3),
    (8, 7, 6, 5, 9, 3, 2, 1, 0, 4),
    (9, 8, 7, 6, 5, 4, 3, 2, 1, 0),
)
_P = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 5, 7, 6, 2, 8, 3, 0, 9, 4),
    (5, 8, 0, 3, 7, 9, 6, 1, 4, 2),
    (8, 9, 1, 6, 0, 4, 3, 5, 2, 7),
    (9, 4, 5, 3, 1, 2, 6, 8, 7, 0),
    (4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
    (2, 7, 9, 3, 8, 0, 6, 4, 1, 5),
    (7, 0, 4, 6, 9, 1, 3, 2, 5, 8),
)
_INV = (0, 4, 3, 2, 1, 5, 6, 7, 8, 9)

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
GENDERS = ("Male", "Female")


def check_digit(digits: str) -> str:
    c = 0
    for i, ch in enumerate(reversed(digits)):
        c = _D[c][_P[(i + 1) % 8][int(ch)]]
    return str(_INV[c])


def is_valid_number(number: str) -> bool:
    c = 0
    for i, ch in enumerate(reversed(number.replace(" ", ""))):
        c = _D[c][_P[i % 8][int(ch)]]
    return c == 0


@dataclass(frozen=True)
class CardSpec:
    card_id: str
    group: str  # "normal" or "wrong_checksum"
    font: str
    effects: tuple[str, ...]
    name: str
    dob: str  # dd/mm/yyyy
    gender: str
    number: str  # as printed: "2345 6789 1008"
    address: str  # without the pin code, as printed after "Address:"
    pincode: str

    def truth(self) -> dict[str, str]:
        """What a correct reading returns: the text as drawn on the card, with the number in its masked form."""
        return {
            "aadhaar_number": "XXXX XXXX " + self.number[-4:],
            "name": self.name,
            "dob": self.dob,
            "gender": self.gender,
            "pincode": self.pincode,
            "address": f"{self.address} {self.pincode}",
        }


def _plan() -> list[tuple[str, tuple[str, ...]]]:
    """(font, defects) of the 100 cards: 10 clean controls, 8 cards of each single defect, 32 with two defects, then the
    2 cards whose number has a wrong check digit."""
    plan: list[tuple[str, tuple[str, ...]]] = [("sans", ())] * 4 + [("serif", ())] * 3 + [("mono", ())] * 3
    plan += [(FONTS[n % 3], (e,)) for e in EFFECTS for n in range(8)]
    plan += [(FONTS[n % 3], pair) for n, pair in enumerate(islice(cycle(combinations(EFFECTS, 2)), 32))]
    return plan + [("sans", ())] * WRONG_CHECKSUM_CARDS


def build_cards() -> list[CardSpec]:
    rng = random.Random(SEED)
    cards: list[CardSpec] = []
    for index, (font, effects) in enumerate(_plan(), start=1):
        group = "wrong_checksum" if index > CARDS - WRONG_CHECKSUM_CARDS else "normal"
        digits = str(rng.randint(2, 9)) + "".join(str(rng.randint(0, 9)) for _ in range(10))
        last = check_digit(digits)
        if group == "wrong_checksum":
            last = str((int(last) + rng.randint(1, 9)) % 10)
        raw = digits + last
        city, state = rng.choice(CITIES)
        cards.append(
            CardSpec(
                card_id=f"A{index:02d}",
                group=group,
                font=font,
                effects=effects,
                name=f"{rng.choice(FIRST_NAMES)} {rng.choice(SURNAMES)}",
                dob=f"{rng.randint(1, 28):02d}/{rng.randint(1, 12):02d}/{rng.randint(1950, 2004)}",
                gender=rng.choice(GENDERS),
                number=f"{raw[:4]} {raw[4:8]} {raw[8:]}",
                address=f"{rng.choice(HOUSES)}, {rng.choice(AREAS)}, {city}, {state}",
                pincode=str(rng.randint(1, 9)) + "".join(rng.choice("0011234567899") for _ in range(5)),
            )
        )
    return cards
