"""Draws one invented Aadhaar-style card and the picture defects of the set (blur, slight rotation, glare, low resolution).

The pictures are drawn when a run starts, from the seed, and are never committed (``cards/`` is git-ignored).
"""

import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from card_set import CardSpec  # noqa: E402

FONT_DIR = Path(__file__).resolve().parents[2] / "fonts"
FAMILIES = {"sans": ("DejaVuSans", "DejaVuSans-Bold"), "serif": ("DejaVuSerif", "DejaVuSerif-Bold"), "mono": ("DejaVuSansMono", "DejaVuSansMono-Bold")}
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
    f = spec.font
    d.text((W // 2, 34), "Government of India", fill="black", font=_font(f, 38, True), anchor="mt")
    d.text((W // 2, 84), "Unique Identification Authority of India (TEST SAMPLE)", fill="#333", font=_font(f, 20), anchor="mt")
    d.rectangle((40, 140, 260, 400), outline="#888", width=3)
    d.text((150, 270), "photograph", fill="#888", font=_font(f, 20), anchor="mm")
    d.text((300, 150), spec.name.title(), fill="black", font=_font(f, 34, True))
    d.text((300, 210), f"DOB: {spec.dob}", fill="black", font=_font(f, 30))
    d.text((300, 258), spec.gender.title(), fill="black", font=_font(f, 30))
    body = _font(f, 24)
    y = 316
    for line in _wrap(d, f"Address: {spec.address.title()} {spec.pincode}", body, 520):
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


def apply_effects(img: Image.Image, effects: tuple[str, ...], seed: int) -> Image.Image:
    rng = random.Random(seed)
    for effect in effects:
        if effect == "blur":
            img = img.filter(ImageFilter.GaussianBlur(1.8))
        elif effect == "rotate":
            img = img.rotate(rng.choice((-1, 1)) * rng.uniform(2.0, 3.5), resample=Image.BICUBIC, fillcolor=PAPER)
        elif effect == "glare":
            img = _glare(img, rng)
        elif effect == "lowres":
            img = img.resize((W * 2 // 5, H * 2 // 5), Image.LANCZOS)  # sent this small, not scaled back up
    return img


def card_picture(spec: CardSpec) -> Image.Image:
    return apply_effects(plain_card(spec), spec.effects, seed=int(spec.card_id[1:]))
