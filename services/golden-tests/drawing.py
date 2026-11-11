"""Draws the made-up documents of the golden tests: page sheets, scan-style copies and ID-card pictures.

Everything printed here is invented (names, numbers, addresses). No real person's data, no real card. The pictures are
drawn when a suite runs and are not committed (they sit in ignored ``pages/`` and ``cards/`` folders).
"""

import random
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from textmatch import words_of

# The fonts ship with the tests (DejaVu, free licence: fonts/LICENSE-DejaVu.txt), so a picture is the same on a laptop, a
# Mac and a VM that has no system fonts, and a result does not move because a host draws the text differently.
FONT = str(Path(__file__).resolve().parent / "fonts" / "DejaVuSans.ttf")
FONT_BOLD = str(Path(__file__).resolve().parent / "fonts" / "DejaVuSans-Bold.ttf")
W, H = 900, 1270
MARGIN = 60


class Sheet:
    def __init__(self, fonts: tuple[str, str] | None = None) -> None:
        self.fonts = fonts or (FONT, FONT_BOLD)  # (regular, bold); DejaVu Sans unless a page asks for another family
        self.boxes: list[tuple[str, float, float, float, float]] = []  # (word, x, y, width, height) as page fractions
        self.img = Image.new("RGB", (W, H), "white")
        self.d = ImageDraw.Draw(self.img)
        self.y = MARGIN
        self.words: list[str] = []
        self.headings: list[str] = []
        self.tables: list[list[list[str]]] = []
        self.allowed_extra: list[str] = []

    def _font(self, size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
        return ImageFont.truetype(self.fonts[1] if bold else self.fonts[0], size)

    def _wrap(self, text: str, font: ImageFont.FreeTypeFont, width: int) -> list[str]:
        lines, cur = [], ""
        for word in text.split():
            trial = f"{cur} {word}".strip()
            if self.d.textlength(trial, font=font) <= width:
                cur = trial
            else:
                lines.append(cur)
                cur = word
        return [*lines, cur]

    def _note(self, text: str, x: float, y: float, font: ImageFont.FreeTypeFont, size: int) -> None:
        """Remember where each word of ``text`` was drawn, as fractions of the page (for OCR-style word lists)."""
        for word in text.split():
            width = self.d.textlength(word, font=font)
            self.boxes.append((word, round(x / W, 4), round(y / H, 4), round(width / W, 4), round(size * 1.25 / H, 4)))
            x += self.d.textlength(word + " ", font=font)

    def heading(self, text: str, size: int = 28, centre: bool = False) -> None:
        font = self._font(size, True)
        x = (W - self.d.textlength(text, font=font)) / 2 if centre else MARGIN
        self._note(text, x, self.y, font, size)
        self.d.text((x, self.y), text, fill="black", font=font)
        self.y += size + 22
        self.words += words_of(text)
        self.headings.append(text)

    def para(self, text: str, size: int = 16, gap: int = 14) -> None:
        font = self._font(size)
        for line in self._wrap(text, font, W - 2 * MARGIN):
            self._note(line, MARGIN, self.y, font, size)
            self.d.text((MARGIN, self.y), line, fill="black", font=font)
            self.y += size + 7
        self.y += gap
        self.words += words_of(text)

    def bullets(self, items: list[str], size: int = 16) -> None:
        font = self._font(size)
        for item in items:
            lines = self._wrap(item, font, W - 2 * MARGIN - 30)
            self.d.text((MARGIN + 6, self.y), "•", fill="black", font=font)
            for line in lines:
                self._note(line, MARGIN + 30, self.y, font, size)
                self.d.text((MARGIN + 30, self.y), line, fill="black", font=font)
                self.y += size + 7
            self.y += 4
            self.words += words_of(item)
        self.y += 10

    def table(self, rows: list[list[str]], size: int = 15) -> None:
        cols = len(rows[0])
        cw = (W - 2 * MARGIN) / cols
        rh = size + 16
        for i, row in enumerate(rows):
            top = self.y + i * rh
            if i == 0:
                self.d.rectangle([MARGIN, top, W - MARGIN, top + rh], fill="#dddddd")
            for j, cell in enumerate(row):
                x0 = MARGIN + j * cw
                self.d.rectangle([x0, top, x0 + cw, top + rh], outline="black", width=1)
                self._note(cell, x0 + 8, top + 8, self._font(size, i == 0), size)
                self.d.text((x0 + 8, top + 8), cell, fill="black", font=self._font(size, i == 0))
                self.words += words_of(cell)
        self.y += len(rows) * rh + 22
        self.tables.append(rows)

    def footer(self, text: str, size: int = 12) -> None:
        font = self._font(size)
        x = (W - self.d.textlength(text, font=font)) / 2
        self.d.text((x, H - 50), text, fill="#333333", font=font)
        self.words += words_of(text)

    def inject(self, text: str, where: str, size: int = 14) -> None:
        y = H - 95 if where == "bottom" else 14
        self.d.rectangle([0, y - 4, W, y + size + 8], fill="white")
        self.d.text((MARGIN, y), text, fill="black", font=self._font(size))
        self.allowed_extra += words_of(text)


def scan_like(img: Image.Image, seed: int) -> Image.Image:
    random.seed(seed)
    tint = Image.new("RGB", img.size, (246, 243, 236))
    out = ImageChops.multiply(img, tint).rotate(random.uniform(-1.4, 1.4), resample=Image.BICUBIC, fillcolor=(246, 243, 236))
    rng = random.Random(seed)  # the noise is seeded too: PIL's own generator would draw a different page on every build
    noise = Image.new("L", out.size)
    noise.putdata([min(255, max(0, int(rng.gauss(128, 22)))) for _ in range(out.size[0] * out.size[1])])
    noise = noise.convert("RGB")
    return Image.blend(out, noise, 0.07).filter(ImageFilter.GaussianBlur(0.8))


# ------------------------------------------------------------------ ID cards (invented values)
AADHAAR_VALID = "2345 6789 1008"  # passes the Verhoeff check digit
AADHAAR_WRONG_CHECK_DIGIT = "2345 6789 1009"  # one digit off: extraction's rule rejects it

CARDS = {
    "PAN_CARD": {
        "title": "INCOME TAX DEPARTMENT   PAN (TEST SAMPLE)",
        "lines": [
            ("Permanent Account Number", "AAAPK1234L"),
            ("Name", "TEST KUMAR"),
            ("Father's Name", "SAMPLE KUMAR"),
            ("Date of Birth", "01/01/1990"),
        ],
        "truth": {"pan_number": "AAAPK1234L", "name": "TEST KUMAR", "father_name": "SAMPLE KUMAR", "dob": "01/01/1990"},
    },
    "AADHAAR_CARD": {
        "title": "AADHAAR (TEST SAMPLE)",
        "lines": [
            ("Name", "TEST KUMAR"),
            ("DOB", "01/01/1990"),
            ("Gender", "MALE"),
            ("Address", "12 Test Street, Test City, Karnataka 560001"),
            ("Aadhaar No", "@@AADHAAR@@"),
        ],
        "truth": {
            "aadhaar_number": "@@AADHAAR@@",
            "name": "TEST KUMAR",
            "dob": "01/01/1990",
            "gender": "MALE",
            "address": "12 Test Street, Test City, Karnataka 560001",
            "pincode": "560001",
        },
    },
    "DRIVING_LICENCE": {
        "title": "DRIVING LICENCE (TEST SAMPLE)",
        "lines": [
            ("DL No", "KA01 20250001234"),
            ("Name", "TEST KUMAR"),
            ("DOB", "01/01/1990"),
            ("Address", "12 Test Street, Test City 560001"),
            ("Issue Date", "05/05/2020"),
            ("Validity NT", "04/05/2040"),
            ("Blood Group", "B+"),
            ("Organ Donor", "N"),
            ("Class", "MCWG, LMV"),
        ],
        "truth": {
            "license_number": "KA01 20250001234",
            "name": "TEST KUMAR",
            "dob": "01/01/1990",
            "address": "12 Test Street, Test City 560001",
            "issue_date": "05/05/2020",
            "validity_nt": "04/05/2040",
            "validity_tr": None,
            "blood_group": "B+",
            "organ_donor": "N",
            "vehicle_classes": "MCWG, LMV",
        },
    },
}


def card_truth(kind: str, aadhaar: str = AADHAAR_VALID) -> dict:
    return {key: (value.replace("@@AADHAAR@@", aadhaar) if isinstance(value, str) else value) for key, value in CARDS[kind]["truth"].items()}


def card_image(kind: str, extra: str | None = None, aadhaar: str = AADHAAR_VALID) -> Image.Image:
    """A plain picture of the card; ``extra`` is a line printed under it (a hostile instruction in the injection tests)."""
    spec = CARDS[kind]
    image = Image.new("RGB", (760, 120 + 42 * len(spec["lines"]) + (40 if extra else 0)), "#f3efe0")
    draw = ImageDraw.Draw(image)
    draw.text((20, 18), spec["title"], fill="black", font=ImageFont.truetype(FONT_BOLD, 20))
    y = 70
    for label, value in spec["lines"]:
        draw.text((20, y), f"{label}:", fill="#444", font=ImageFont.truetype(FONT, 15))
        draw.text((250, y), value.replace("@@AADHAAR@@", aadhaar), fill="black", font=ImageFont.truetype(FONT, 17))
        y += 42
    if extra:
        draw.text((20, y + 6), extra, fill="black", font=ImageFont.truetype(FONT, 13))
    return image
