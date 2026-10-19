"""Light, reversible-by-fallback preprocessing: find the card, straighten it, try 90/180/270 turns.

Nothing here thresholds, denoises or sharpens the pixels that are fed to OCR - only geometry
(crop / perspective warp / rotation) changes, so small characters and punctuation are untouched.
"""
import cv2
import numpy as np


def order_points(pts):
    pts = np.array(pts, np.float32)
    s, d = pts.sum(1), np.diff(pts, axis=1).ravel()
    return np.array([pts[s.argmin()], pts[d.argmin()], pts[s.argmax()], pts[d.argmax()]], np.float32)  # tl tr br bl


def find_card(img):
    """Largest card-shaped quadrilateral (aspect ~1.3-1.9) covering 15-92% of the image, else None."""
    h, w = img.shape[:2]
    s = 800 / max(h, w)
    small = cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA) if s < 1 else img
    sh, sw = small.shape[:2]
    gray = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_RGB2GRAY), (5, 5), 0)
    edges = cv2.dilate(cv2.Canny(gray, 30, 100), np.ones((3, 3), np.uint8), iterations=2)
    cnts, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for c in sorted(cnts, key=cv2.contourArea, reverse=True)[:5]:
        frac = cv2.contourArea(c) / (sh * sw)
        if frac < 0.15:
            break
        if frac > 0.92:
            continue  # the image already is the card
        hull = cv2.convexHull(c)
        approx = cv2.approxPolyDP(hull, 0.02 * cv2.arcLength(hull, True), True)
        quad = approx.reshape(-1, 2) if len(approx) == 4 else cv2.boxPoints(cv2.minAreaRect(hull))
        q = order_points(quad)
        wd = max(np.linalg.norm(q[1] - q[0]), np.linalg.norm(q[2] - q[3]))
        ht = max(np.linalg.norm(q[3] - q[0]), np.linalg.norm(q[2] - q[1]))
        aspect = max(wd, ht) / max(1.0, min(wd, ht))
        if 1.3 <= aspect <= 1.9 and cv2.contourArea(hull) / max(1.0, cv2.contourArea(q)) > 0.85:
            return q / s
    return None


def warp_card(img, quad):
    wd = int(max(np.linalg.norm(quad[1] - quad[0]), np.linalg.norm(quad[2] - quad[3])))
    ht = int(max(np.linalg.norm(quad[3] - quad[0]), np.linalg.norm(quad[2] - quad[1])))
    dst = np.float32([[0, 0], [wd, 0], [wd, ht], [0, ht]])
    out = cv2.warpPerspective(img, cv2.getPerspectiveTransform(quad, dst), (wd, ht), flags=cv2.INTER_CUBIC)
    return np.rot90(out).copy() if ht > wd else out


def estimate_skew(img, max_deg=10):
    """Angle (degrees, cv2 convention) that makes text rows sharpest, by row-profile search."""
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    s = 700 / max(gray.shape)
    small = cv2.resize(gray, None, fx=s, fy=s, interpolation=cv2.INTER_AREA) if s < 1 else gray
    bw = cv2.adaptiveThreshold(small, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 25, 15)
    h, w = bw.shape

    def score(a):
        m = cv2.getRotationMatrix2D((w / 2, h / 2), a, 1)
        prof = cv2.warpAffine(bw, m, (w, h)).sum(1).astype(np.float64)
        return float(np.sum(np.diff(prof) ** 2))

    coarse = max(np.arange(-max_deg, max_deg + 0.1, 1.0), key=score)
    fine = max(np.arange(coarse - 0.75, coarse + 0.76, 0.25), key=score)
    return 0.0 if score(fine) < 1.03 * score(0.0) else float(fine)  # ignore noise-level gains


def rotate(img, angle):
    """Rotate by an arbitrary angle, growing the canvas so nothing is cut off."""
    if abs(angle) < 0.5:
        return img
    h, w = img.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1)
    c, s = abs(m[0, 0]), abs(m[0, 1])
    nw, nh = int(h * s + w * c), int(h * c + w * s)
    m[0, 2] += nw / 2 - w / 2
    m[1, 2] += nh / 2 - h / 2
    border = tuple(int(v) for v in np.median(img[:: max(1, h // 20), :: max(1, w // 20)].reshape(-1, 3), axis=0))
    return cv2.warpAffine(img, m, (nw, nh), flags=cv2.INTER_CUBIC, borderValue=border)


def prepare(img):
    """Base image for OCR plus a short, non-identifying note of what was done."""
    quad = find_card(img)
    if quad is not None:
        base, note = warp_card(img, quad), "card cropped"
    else:
        base, note = img, "no card quad"
    angle = estimate_skew(base)
    if angle:
        base, note = rotate(base, angle), f"{note}, deskew {angle:+.2f}deg"
    return base, note
