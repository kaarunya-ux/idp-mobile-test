"""The 30 invented Aadhaar cards of the accuracy set and the value each field should be read as.

Nothing here is a real person or a real number: names are built from made-up surnames, numbers are random 12-digit
strings given a Verhoeff check digit. 28 cards carry a valid number and are scored for accuracy; 2 carry a number with
a wrong check digit and are their own class: the right outcome for them is that the number is not accepted.
"""

import random
from dataclasses import dataclass

SEED = 20261111
FIELDS = ("aadhaar_number", "name", "dob", "gender", "pincode", "address")
FONTS = ("sans", "serif", "mono")
EFFECTS = ("blur", "rotate", "glare", "lowres")

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

FIRST_NAMES = ("Anil", "Meera", "Rohit", "Sunita", "Karan", "Divya", "Manoj", "Pooja", "Vikas", "Neha", "Arjun", "Kavita", "Sanjay", "Ritu")
SURNAMES = ("Testrao", "Samplekar", "Demoji", "Mockwal", "Fakepatil", "Dummyani", "Trialkar", "Sandboxe", "Pretendi", "Mockhade")
AREAS = ("Sample Nagar", "Demo Colony", "Test Layout", "Mock Enclave", "Trial Road", "Dummy Park")
CITIES = (("Testpur", "Karnataka"), ("Mockabad", "Maharashtra"), ("Samplegarh", "Tamil Nadu"), ("Demopet", "Telangana"))
GENDERS = ("MALE", "FEMALE")


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
        """What a correct reading returns, with the number in the masked form the service returns."""
        return {
            "aadhaar_number": "XXXX XXXX " + self.number[-4:],
            "name": self.name,
            "dob": self.dob,
            "gender": self.gender,
            "pincode": self.pincode,
            "address": f"{self.address} {self.pincode}",
        }


def _plan() -> list[tuple[str, tuple[str, ...]]]:
    """(font, effects) of the 30 cards, in order: 6 plain, 4 serif, 4 mono, 3 each of blur, rotation, glare and low
    resolution, 2 with two effects together, then the 2 cards whose number has a wrong check digit."""
    plan = [("sans", ())] * 6 + [("serif", ())] * 4 + [("mono", ())] * 4
    plan += [("sans", (e,)) for e in EFFECTS for _ in range(3)]
    plan += [("sans", ("blur", "rotate")), ("serif", ("glare", "lowres"))]
    return plan + [("sans", ())] * 2


def build_cards() -> list[CardSpec]:
    rng = random.Random(SEED)
    cards: list[CardSpec] = []
    for index, (font, effects) in enumerate(_plan(), start=1):
        group = "wrong_checksum" if index > 28 else "normal"
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
                name=f"{rng.choice(FIRST_NAMES)} {rng.choice(SURNAMES)}".upper(),
                dob=f"{rng.randint(1, 28):02d}/{rng.randint(1, 12):02d}/{rng.randint(1950, 2004)}",
                gender=rng.choice(GENDERS),
                number=f"{raw[:4]} {raw[4:8]} {raw[8:]}",
                address=f"{rng.randint(1, 99)}, {rng.choice(AREAS)}, {city}, {state}".upper(),
                pincode=str(rng.randint(100000, 999999)),
            )
        )
    return cards
