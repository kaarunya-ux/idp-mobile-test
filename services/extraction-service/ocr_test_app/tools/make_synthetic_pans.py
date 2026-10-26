"""Generate English-only synthetic PAN-style test cards (fictional data, marked SPECIMEN) into pan_test_images/.

    python tools/make_synthetic_pans.py        # writes synthetic_pan_*.webp + synthetic_pan_truth.json

The truth file lists the values printed on each card so results can be checked by eye or script.
"""
import json
import os

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

OUT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "pan_test_images"))
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans{}.ttf"
SERIF = "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"
W, H = 860, 540

# (file suffix, PAN, name, father, dob, variant)
CARDS = [
    ("01_clean", "ABCPK1234E", "ARAVIND KUMAR", "SURESH KUMAR", "14/03/1992", "clean"),
    ("02_long_names", "BQRPM5821L", "PRIYADARSHINI VENKATESWARAN", "RAMAKRISHNAN IYER", "29/11/1987", "clean"),
    ("03_rotated", "CDEPT4096G", "MOHAMMED FAISAL KHAN", "ABDUL RAHIM KHAN", "07/08/1999", "rotate"),
    ("04_blur", "DFGPS7310H", "NEHA SHARMA", "RAJESH SHARMA", "21/01/2001", "blur"),
    ("05_dark_noise", "EHJPV2649A", "TARUN PATEL", "HASMUKH PATEL", "30/06/1985", "dark"),
    ("06_perspective", "FKLPD8153C", "ANJALI MENON", "GOPALAN MENON", "11/12/1995", "perspective"),
    ("07_low_res", "GMNPR3972J", "VIKRAM SINGH RATHORE", "BHANWAR SINGH RATHORE", "03/04/1990", "lowres"),
]


def font(size, bold=False, serif=False):
    return ImageFont.truetype(SERIF if serif else FONT.format("-Bold" if bold else ""), size)


def qr(text, size):
    """QR code via OpenCV's own encoder (no extra packages)."""
    m = cv2.QRCodeEncoder.create().encode(text)
    m = cv2.resize(m, (size, size), interpolation=cv2.INTER_NEAREST)
    return Image.fromarray(m).convert("RGB")


def card(pan, name, father, dob):
    im = Image.new("RGB", (W, H))
    px = np.zeros((H, W, 3), np.uint8)
    for x in range(W):  # light blue gradient like the real stock
        px[:, x] = (int(150 + 70 * x / W), int(200 + 40 * x / W), 245)
    im = Image.fromarray(px)
    d = ImageDraw.Draw(im)
    ink, blue = (20, 20, 30), (25, 45, 110)
    d.text((28, 60), "INCOME TAX DEPARTMENT", font=font(26, serif=True), fill=ink)
    d.text((W - 28, 60), "GOVT. OF INDIA", font=font(26, serif=True), fill=ink, anchor="ra")
    d.text((200, 150), "Permanent Account Number Card", font=font(22), fill=blue)
    d.text((222, 190), pan, font=font(34, bold=True), fill=ink)
    d.rectangle((28, 120, 178, 270), fill=(120, 130, 150))  # photo placeholder (silhouette)
    d.ellipse((75, 135, 131, 195), fill=(180, 185, 195))
    d.ellipse((50, 200, 156, 300), fill=(180, 185, 195))
    d.rectangle((28, 270, 178, 272), fill=(120, 130, 150))
    d.text((28, 290), "Name", font=font(20), fill=blue)
    d.text((28, 314), name, font=font(24, bold=True), fill=ink)
    d.text((28, 362), "Father's Name", font=font(20), fill=blue)
    d.text((28, 386), father, font=font(24, bold=True), fill=ink)
    d.text((28, 434), "Date of Birth", font=font(20), fill=blue)
    d.text((28, 458), dob, font=font(24, bold=True), fill=ink)
    pts = [(420, 480), (450, 450), (470, 490), (505, 446), (530, 488), (570, 460)]  # scribble signature
    d.line(pts, fill=(15, 15, 90), width=3)
    d.text((440, 495), "Signature", font=font(20), fill=blue)
    im.paste(qr("SPECIMEN TEST CARD " + pan, 236), (590, 120))
    d.text((630, 365), "23082021", font=font(18), fill=ink)
    d.text((28, 515), "SPECIMEN - FICTIONAL DATA FOR OCR TESTING ONLY", font=font(13), fill=(90, 90, 100))
    return np.array(im)


def scene(img, angle=0, persp=False, bg=(70, 60, 55)):
    """Place the card on a background like a phone photo."""
    canvas = np.empty((H + 260, W + 260, 3), np.uint8)
    canvas[:] = bg
    canvas += np.random.RandomState(1).randint(0, 20, canvas.shape, dtype=np.uint8)
    canvas[130:130 + H, 130:130 + W] = img
    if persp:
        src = np.float32([[130, 130], [130 + W, 130], [130 + W, 130 + H], [130, 130 + H]])
        dst = np.float32([[170, 150], [130 + W - 20, 120], [130 + W + 10, 130 + H - 10], [120, 130 + H + 25]])
        return cv2.warpPerspective(canvas, cv2.getPerspectiveTransform(src, dst), canvas.shape[1::-1], borderValue=bg)
    m = cv2.getRotationMatrix2D((canvas.shape[1] / 2, canvas.shape[0] / 2), angle, 1)
    return cv2.warpAffine(canvas, m, canvas.shape[1::-1], borderValue=bg)


def main():
    truth = {}
    for suffix, pan, name, father, dob, variant in CARDS:
        img = card(pan, name, father, dob)
        if variant == "rotate":
            img = scene(img, angle=4)
        elif variant == "perspective":
            img = scene(img, persp=True)
        elif variant == "blur":
            img = cv2.GaussianBlur(img, (0, 0), 1.6)
        elif variant == "dark":
            img = np.clip(img.astype(np.float32) * 0.55 + 8, 0, 255)
            img = np.clip(img + np.random.RandomState(2).normal(0, 9, img.shape), 0, 255).astype(np.uint8)
        elif variant == "lowres":
            img = cv2.resize(img, (480, 300), interpolation=cv2.INTER_AREA)
        fname = f"synthetic_pan_{suffix}.webp"
        Image.fromarray(img).save(os.path.join(OUT, fname), quality=90)
        truth[fname] = {"pan_number": pan, "name": name, "father_name": father, "dob": dob}
        print("wrote", fname, img.shape[1], "x", img.shape[0])
    with open(os.path.join(os.path.dirname(__file__), "synthetic_pan_truth.json"), "w") as f:
        json.dump(truth, f, indent=2)
        f.write("\n")


if __name__ == "__main__":
    main()
