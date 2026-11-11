"""Draws the extraction injection cards: each of three invented ID cards plain, and with each hostile line printed under
it (3 x 7 pictures) into ``cards/`` (not committed).

    python build.py
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))  # services/golden-tests
sys.path.insert(0, str(HERE))
from attacks import ATTACKS  # noqa: E402
from drawing import CARDS, card_image  # noqa: E402


def build() -> None:
    (HERE / "cards").mkdir(exist_ok=True)
    for kind in CARDS:
        card_image(kind).save(HERE / "cards" / f"{kind}__normal.png")
        for name, line in ATTACKS.items():
            card_image(kind, line).save(HERE / "cards" / f"{kind}__{name}.png")
    print("built", len(list((HERE / "cards").glob("*.png"))), "cards")


if __name__ == "__main__":
    build()
