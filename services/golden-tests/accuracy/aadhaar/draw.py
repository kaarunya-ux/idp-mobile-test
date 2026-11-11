"""Draws one invented Aadhaar-style card and the picture defects of the set: angle, strong blur, faded print, glare, low
resolution, a crop that cuts a field edge, and the card lying on a table.

The pictures are drawn when a run starts, from the seed, and are never committed (``cards/`` is git-ignored).
"""

import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from card_set import CardSpec  # noqa: E402

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


def _font(family: str, size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_DIR / f"{FAMILIES[family][1 if bold else 0]}.ttf"), size)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, width: int) -> list[str]:
    lines, current = [], ""
    for word in text.split():
        if current and draw.textlength(f"{current} {word}", font=font) > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    return [*lines, current]


def plain_card(spec: CardSpec) -> Image.Image:
    img = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(img)
    f, t = spec.font, spec.truth()
    d.text((W // 2, 34), "Government of India", fill="black", font=_font(f, 38, True), anchor="mt")
    d.text((W // 2, 84), "Unique Identification Authority of India (TEST SAMPLE)", fill="#333", font=_font(f, 20), anchor="mt")
    d.rectangle((40, 140, 260, 400), outline="#888", width=3)
    d.text((150, 270), "photograph", fill="#888", font=_font(f, 20), anchor="mm")
    d.text((300, 150), t["name"], fill="black", font=_font(f, 34, True))
    d.text((300, 210), f"DOB: {spec.dob}", fill="black", font=_font(f, 30))
    d.text((300, 258), t["gender"], fill="black", font=_font(f, 30))
    body = _font(f, 24)
    y = 316
    for line in _wrap(d, f"Address: {t['address']}", body, 520):
        d.text((300, y), line, fill="black", font=body)
        y += 34
    d.rectangle((790, 140, 960, 310), outline="#888", width=3)
    d.text((875, 225), "QR code", fill="#888", font=_font(f, 20), anchor="mm")
    d.text((W // 2, 520), spec.number, fill="black", font=_font(f, 56, True), anchor="mm")
    d.line((40, 570, W - 40, 570), fill="#c33", width=4)
    return img


def _glare(img: Image.Image, rng: random.Random) -> Image.Image:
    mask = Image.new("L", img.size, 0)
    cx, cy = rng.randint(250, 750), rng.randint(150, 450)
    ImageDraw.Draw(mask).ellipse((cx - 190, cy - 110, cx + 190, cy + 110), fill=235)
    return Image.composite(Image.new("RGB", img.size, "white"), img, mask.filter(ImageFilter.GaussianBlur(45)))


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
    drop = rng.uniform(0.10, 0.17) * H
    left, right = ((0, H), (drop, H - drop)) if rng.random() < 0.5 else ((drop, H - drop), (0, H))
    dst = [(0, left[0]), (W, right[0]), (W, right[1]), (0, left[1])]
    corners = [(0, 0), (W, 0), (W, H), (0, H)]
    return img.transform(img.size, Image.PERSPECTIVE, _perspective(corners, dst), Image.BICUBIC, fillcolor=PAPER)


def _table(img: Image.Image, rng: random.Random) -> Image.Image:
    """The card lying a little askew on a wooden table, with room around it."""
    scene = Image.new("RGB", (W + 360, H + 300), TABLE)
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
            img = img.crop((0, 0, rng.randint(740, 800), img.height))  # the edge falls through the end of the address
        elif effect == "table":
            img = _table(img, rng)
        elif effect == "lowres":
            img = img.resize((img.width * 2 // 5, img.height * 2 // 5), Image.LANCZOS)  # sent this small, not scaled back up
    return img


def card_picture(spec: CardSpec) -> Image.Image:
    return apply_effects(plain_card(spec), spec.effects, seed=int(spec.card_id[1:]))
