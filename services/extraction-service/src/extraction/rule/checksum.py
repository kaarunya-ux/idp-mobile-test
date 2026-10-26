"""The Verhoeff checksum, which Aadhaar numbers carry as their last digit (IDP-03 ch. 2.5, rule kind CHECKSUM).

It detects every single-digit error and every swap of two neighbouring digits.
"""

_MULTIPLICATION = (
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
_PERMUTATION = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 5, 7, 6, 2, 8, 3, 0, 9, 4),
    (5, 8, 0, 3, 7, 9, 6, 1, 4, 2),
    (8, 9, 1, 6, 0, 4, 3, 5, 2, 7),
    (9, 4, 5, 3, 1, 2, 6, 8, 7, 0),
    (4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
    (2, 7, 9, 3, 8, 0, 6, 4, 1, 5),
    (7, 0, 4, 6, 9, 1, 3, 2, 5, 8),
)
_INVERSE = (0, 4, 3, 2, 1, 5, 6, 7, 8, 9)
_PERMUTATION_ROWS = len(_PERMUTATION)


def is_verhoeff_valid(number: str) -> bool:
    """Whether ``number`` (digits only, check digit last) passes the checksum. Anything else is invalid."""
    if not number or not (number.isascii() and number.isdigit()):
        return False
    running = 0
    for position, digit in enumerate(reversed(number)):
        running = _MULTIPLICATION[running][_PERMUTATION[position % _PERMUTATION_ROWS][int(digit)]]
    return running == 0


def calculate_verhoeff_check_digit(payload: str) -> str:
    """The digit to append to ``payload`` (digits only) so that the result passes ``is_verhoeff_valid``."""
    if not (payload.isascii() and payload.isdigit()):
        message = "The Verhoeff payload must be digits only."
        raise ValueError(message)
    running = 0
    for position, digit in enumerate(reversed(payload), start=1):
        running = _MULTIPLICATION[running][_PERMUTATION[position % _PERMUTATION_ROWS][int(digit)]]
    return str(_INVERSE[running])
