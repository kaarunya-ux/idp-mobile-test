"""Draws the five invented documents into ``pages/`` (not committed): a PAN card, an Aadhaar card, an Aadhaar card whose
number fails the check digit, a driving licence, and a letter with a phone number, an e-mail address and a PAN.

    python build.py
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))  # services/golden-tests
from drawing import AADHAAR_VALID, AADHAAR_WRONG_CHECK_DIGIT, Sheet, card_image  # noqa: E402

(HERE / "pages").mkdir(exist_ok=True)
card_image("PAN_CARD").save(HERE / "pages" / "P01_pan.png")
card_image("AADHAAR_CARD", aadhaar=AADHAAR_VALID).save(HERE / "pages" / "P02_aadhaar.png")
card_image("AADHAAR_CARD", aadhaar=AADHAAR_WRONG_CHECK_DIGIT).save(HERE / "pages" / "P03_aadhaar_badsum.png")
card_image("DRIVING_LICENCE").save(HERE / "pages" / "P04_licence.png")

letter = Sheet()
letter.heading("Orchid Trading Company", 28, centre=True)
letter.para("12 Market Lane, Nashik 422001", 14)
letter.para("Contact: A. Mehta, phone +91 98765 43210, e-mail a.mehta@greenfield.example")
letter.para("PAN of the firm: AAAPK1234L. Please quote it in every reply.")
letter.para("We confirm receipt of your remittance and will dispatch the goods within seven working days of today.")
letter.para("Yours faithfully, A. Mehta")
letter.img.save(HERE / "pages" / "P05_letter.png")
print("built", len(list((HERE / "pages").glob("*.png"))), "documents")
