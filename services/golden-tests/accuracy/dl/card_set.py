"""The 100 invented driving licence cards of the accuracy set and the value each scored field should be read as.

Nothing here is a real person or a real licence: names are built from made-up surnames, numbers follow the service's rule
``[A-Z]{2}-?[0-9]{2}(19|20)[0-9]{2}[0-9]{7}`` (state, RTO, issue year, seven-digit serial). A licence has a front (number, name, dates,
blood group) and a back (address, vehicle classes). Half the cards are sent as one picture with the front on the left and the back on
the right (``side_by_side``), half as two pictures, a front and a back (``two_pictures``). 98 cards carry a valid number; 2 carry a serial
a digit short and are their own class: the right outcome is that the number is not accepted (NEEDS_REVIEW). Scored: number, name, address.
"""

import random
from dataclasses import dataclass
from itertools import combinations, cycle, islice

SEED = 20261113
FIELDS = ("license_number", "name", "address")
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
CITIES = (
    ("Testpur", "Karnataka"),
    ("Clarpur", "Karnataka"),
    ("Samplegarh", "Haryana"),
    ("Marnal", "Haryana"),
    ("Demopet", "Chandigarh"),
    ("Dolmahal", "Chandigarh"),
    ("Mockabad", "Maharashtra"),
    ("Clarnagar", "Tamil Nadu"),
    ("Mornpura", "Gujarat"),
    ("Lalmor", "Telangana"),
)
STATES = ("KA", "HR", "CH", "MH", "DL", "TN", "GJ", "TS")
BLOOD_GROUPS = ("O+", "A+", "B+", "AB+", "O-", "A-", "B-", "AB-")
CLASSES = ("MCWG LMV", "LMV", "MCWG", "LMV HMV", "MCWOG LMV")


@dataclass(frozen=True)
class CardSpec:
    card_id: str
    group: str  # "normal" or "invalid_number" (the serial of the number is a digit short)
    font: str
    effects: tuple[str, ...]
    layout: str  # "side_by_side" or "two_pictures"
    name: str
    father: str
    dob: str
    issue: str
    valid_till: str
    number: str  # as printed: "KA01 20190001234", "KA0120190001234" or "KA-0120190001234"
    blood: str
    classes: str
    address: str  # with the pin code

    def truth(self) -> dict[str, str]:
        """The scored fields. The number is as the service returns it: upper case, spaces removed."""
        return {"license_number": self.number.replace(" ", "").upper(), "name": self.name.upper(), "address": self.address.upper()}


def _plan() -> list[tuple[str, tuple[str, ...]]]:
    plan: list[tuple[str, tuple[str, ...]]] = [("sans", ())] * 4 + [("serif", ())] * 3 + [("mono", ())] * 3
    plan += [(FONTS[n % 3], (e,)) for e in EFFECTS for n in range(8)]
    plan += [(FONTS[n % 3], pair) for n, pair in enumerate(islice(cycle(combinations(EFFECTS, 2)), 32))]
    return plan + [("sans", ())] * WRONG_CARDS


def build_cards() -> list[CardSpec]:
    rng = random.Random(SEED)
    cards = []
    for index, (font_name, effects) in enumerate(_plan(), start=1):
        surname = rng.choice(SURNAMES)
        dob_year = rng.randint(1965, 1995)
        issue_year = rng.randint(max(2012, dob_year + 19), 2024)  # an adult, and still valid today (20 years)
        serial = "".join(rng.choice("0011234567899") for _ in range(7))
        if index > CARDS - WRONG_CARDS:
            serial = serial[:-1]
        code = f"{rng.choice(STATES)}{rng.randint(1, 99):02d}"
        number = rng.choice((f"{code} {issue_year}{serial}", f"{code}{issue_year}{serial}", f"{code[:2]}-{code[2:]}{issue_year}{serial}"))
        city, state = rng.choice(CITIES)
        pin = str(rng.randint(1, 9)) + "".join(rng.choice("0011234567899") for _ in range(5))
        cards.append(
            CardSpec(
                card_id=f"D{index:02d}",
                group="invalid_number" if index > CARDS - WRONG_CARDS else "normal",
                font=font_name,
                effects=effects,
                layout="side_by_side" if index % 2 else "two_pictures",
                name=f"{rng.choice(FIRST_NAMES)} {surname}",
                father=f"{rng.choice(FIRST_NAMES)} {surname}",
                dob=f"{rng.randint(1, 28):02d}/{rng.randint(1, 12):02d}/{dob_year}",
                issue=f"{rng.randint(1, 28):02d}/{rng.randint(1, 12):02d}/{issue_year}",
                valid_till=f"{rng.randint(1, 28):02d}/{rng.randint(1, 12):02d}/{issue_year + 20}",
                number=number,
                blood=rng.choice(BLOOD_GROUPS),
                classes=rng.choice(CLASSES),
                address=f"{rng.choice(HOUSES)}, {rng.choice(AREAS)}, {city}, {state} {pin}",
            )
        )
    return cards
