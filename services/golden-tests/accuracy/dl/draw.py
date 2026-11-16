"""Draws one invented driving licence card and the picture defects of the set: angle, strong blur, faded print, glare, low
resolution, a crop that cuts a field edge, and the card lying on a table.

The pictures are drawn when a run starts, from the seed, and are never committed (``cards/`` is git-ignored).
"""

import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dl.card_set import CardSpec  # noqa: E402

FONT_DIR = Path(__file__).resolve().parents[2] / "fonts"
FAMILIES = {
    "sans": ("DejaVuSans", "DejaVuSans-Bold"),
    "serif": ("DejaVuSerif", "DejaVuSerif-Bold"),
    "mono": ("DejaVuSansMono", "DejaVuSansMono-Bold"),
}
ORDER = ("faded", "strong_blur", "glare", "angle", "crop", "table", "lowres")  # the order the defects are applied in
TABLE = (118, 84, 56)
W, H = 1000, 630  # the aspect of a real card, 1.586
PAPER = (247, 244, 236)


def font(family: str, size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_DIR / f"{FAMILIES[family][1 if bold else 0]}.ttf"), size)


def wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, width: int) -> list[str]:
    lines, current = [], ""
    for word in text.split():
        if current and draw.textlength(f"{current} {word}", font=font) > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    return [*lines, current]


GAP = 24  # between the two sides of a side-by-side picture
SHEET = (170, 170, 170)


def front(spec: CardSpec) -> Image.Image:
    img = Image.new("RGB", (W, H), "#e6efe2")
    d, f, t = ImageDraw.Draw(img), spec.font, spec.truth()
    d.text((W // 2, 30), "DRIVING LICENCE", fill="#1d4f2a", font=font(f, 38, True), anchor="mt")
    d.text((W // 2, 80), "Transport Department - Union of India (TEST SAMPLE)", fill="#333", font=font(f, 20), anchor="mt")
    d.rectangle((40, 140, 260, 400), outline="#888", width=3, fill=PAPER)
    d.text((290, 140), f"DL No: {spec.number}", fill="black", font=font(f, 32, True))
    for y, line in zip(
        (200, 252, 300, 348, 396, 444),
        (
            f"Name: {t['name']}",
            f"S/D/W of: {spec.father.upper()}",
            f"DOB: {spec.dob}",
            f"Issue Date: {spec.issue}",
            f"Valid Till: {spec.valid_till}",
            f"Blood Group: {spec.blood}",
        ),
        strict=True,
    ):
        d.text((290, y), line, fill="black", font=font(f, 28))
    d.text((40, 560), "Signature of holder", fill="#555", font=font(f, 20))
    return img


def back(spec: CardSpec) -> Image.Image:
    img = Image.new("RGB", (W, H), "#e6efe2")
    d, f, t = ImageDraw.Draw(img), spec.font, spec.truth()
    d.text((40, 40), "Address:", fill="black", font=font(f, 26, True))
    body, y = font(f, 28), 90
    for line in wrap(d, t["address"], body, 900):
        d.text((40, y), line, fill="black", font=body)
        y += 40
    d.text((40, 300), f"Vehicle classes: {spec.classes}", fill="black", font=font(f, 28))
    d.rectangle((40, 400, 300, 560), outline="#888", width=3)
    d.text((170, 480), "QR code", fill="#888", font=font(f, 20), anchor="mm")
    return img


def pictures(spec: CardSpec) -> list[Image.Image]:
    seed = int(spec.card_id[1:])
    if spec.layout == "two_pictures":
        return [apply_effects(front(spec), spec.effects, seed), apply_effects(back(spec), spec.effects, seed + 1000)]
    sheet = Image.new("RGB", (2 * W + GAP, H), SHEET)
    sheet.paste(front(spec), (0, 0))
    sheet.paste(back(spec), (W + GAP, 0))
    return [apply_effects(sheet, spec.effects, seed)]


def _perspective(src: list[tuple[float, float]], dst: list[tuple[float, float]]) -> list[float]:
    """The 8 coefficients PIL wants to map the output corners ``dst`` back onto the input corners ``src``."""
    m = []
    for (x, y), (u, v) in zip(dst, src, strict=True):
        m += [[x, y, 1, 0, 0, 0, -u * x, -u * y, u], [0, 0, 0, x, y, 1, -v * x, -v * y, v]]
    for i in range(8):
        pivot = max(range(i, 8), key=lambda r: abs(m[r][i]))
        m[i], m[pivot] = m[pivot], m[i]
        m[i] = [value / m[i][i] for value in m[i]]
        m = [row if r == i else [a - row[i] * b for a, b in zip(row, m[i], strict=True)] for r, row in enumerate(m)]
    return [row[8] for row in m]


def _angle(img: Image.Image, rng: random.Random) -> Image.Image:
    """Photo taken at an angle: one side of the card is nearer the camera, so the other side is shorter."""
    w, h = img.size
    drop = rng.uniform(0.10, 0.17) * h
    left, right = ((0, h), (drop, h - drop)) if rng.random() < 0.5 else ((drop, h - drop), (0, h))
    dst = [(0, left[0]), (w, right[0]), (w, right[1]), (0, left[1])]
    corners = [(0, 0), (w, 0), (w, h), (0, h)]
    return img.transform(img.size, Image.PERSPECTIVE, _perspective(corners, dst), Image.BICUBIC, fillcolor=PAPER)


def _table(img: Image.Image, rng: random.Random) -> Image.Image:
    """The card lying a little askew on a wooden table, with room around it."""
    scene = Image.new("RGB", (max(img.width, W) + 360, max(img.height, H) + 300), TABLE)
    grain = ImageDraw.Draw(scene)
    for y in range(0, scene.height, 6):
        shade = rng.randint(-14, 14)
        grain.line((0, y, scene.width, y), fill=tuple(c + shade for c in TABLE), width=3)
    card = img.rotate(rng.uniform(-9, 9), resample=Image.BICUBIC, expand=True, fillcolor=TABLE)
    scene.paste(card, ((scene.width - card.width) // 2, (scene.height - card.height) // 2))
    return scene


def _glare(img: Image.Image, rng: random.Random) -> Image.Image:
    mask = Image.new("L", img.size, 0)
    cx, cy = rng.randint(250, 750), rng.randint(150, 450)
    ImageDraw.Draw(mask).ellipse((cx - 230, cy - 140, cx + 230, cy + 140), fill=255)
    return Image.composite(Image.new("RGB", img.size, "white"), img, mask.filter(ImageFilter.GaussianBlur(45)))


def apply_effects(img: Image.Image, effects: tuple[str, ...], seed: int) -> Image.Image:
    rng = random.Random(seed)
    for effect in sorted(effects, key=ORDER.index):
        if effect == "faded":
            img = Image.blend(ImageEnhance.Contrast(img).enhance(0.35), Image.new("RGB", img.size, PAPER), 0.25)
        elif effect == "strong_blur":
            img = img.filter(ImageFilter.GaussianBlur(4.5))
        elif effect == "angle":
            img = _angle(img, rng)
        elif effect == "glare":
            img = _glare(img, rng)
        elif effect == "crop":
            img = img.crop((0, 0, rng.randint(740, 800) * img.width // W, img.height))  # the edge falls through the end of the address
        elif effect == "table":
            img = _table(img, rng)
        elif effect == "lowres":
            img = img.resize((img.width * 2 // 5, img.height * 2 // 5), Image.LANCZOS)  # sent this small, not scaled back up
    return img
