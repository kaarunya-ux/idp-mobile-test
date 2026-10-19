"""Template-guided field extraction.

A template (card_templates/*.json) says which fields a card has, how to locate each one
(regex over all lines, or relative to a label line) and what a valid value looks like.
A value is only returned if it is found where the template expects it AND passes its
format/checksum/confidence rules; otherwise the field is reported as rejected/missing
with the reason, instead of passing OCR noise through as an answer.
"""
import glob
import json
import os
import re

import cv2
import numpy as np

from hindi import clean_pin_and_care_of

TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "card_templates")

# ---- fixers / validators -----------------------------------------------------------

_TO_DIGIT = {"O": "0", "D": "0", "Q": "0", "I": "1", "L": "1", "Z": "2", "S": "5", "B": "8", "G": "6"}
_TO_ALPHA = {v: k for k, v in {"O": "0", "I": "1", "Z": "2", "S": "5", "B": "8", "G": "6"}.items()}


def fix_pan_positional(s):
    """PAN is AAAAA9999A: letters at 0-4 and 9, digits at 5-8. Swap look-alikes by position."""
    s = re.sub(r"[^A-Z0-9]", "", s.upper())
    if len(s) != 10:
        return s
    out = []
    for i, ch in enumerate(s):
        if 5 <= i <= 8:
            out.append(_TO_DIGIT.get(ch, ch))
        else:
            out.append(_TO_ALPHA.get(ch, ch))
    return "".join(out)


_VD = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5], [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
       [3, 4, 0, 1, 2, 8, 9, 5, 6, 7], [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
       [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3], [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
       [9, 8, 7, 6, 5, 4, 3, 2, 1, 0]]
_VP = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4], [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
       [8, 9, 1, 6, 0, 4, 3, 5, 2, 7], [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
       [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8]]


def verhoeff_ok(num):
    c = 0
    for i, d in enumerate(reversed(num)):
        c = _VD[c][_VP[i % 8][int(d)]]
    return c == 0


def validate(kind, value):
    """Returns an error string, or None if the value is acceptable."""
    if kind == "pan":
        if not re.fullmatch(r"[A-Z]{3}[ABCFGHJLPT][A-Z][0-9]{4}[A-Z]", value):
            return "not a valid PAN (4th letter must be one of ABCFGHJLPT)"
    elif kind == "aadhaar":
        if value.startswith("XXXX XXXX "):
            return None if re.fullmatch(r"XXXX XXXX [0-9]{4}", value) else "malformed masked Aadhaar"
        return validate("verhoeff", value)
    elif kind == "verhoeff":
        if not re.fullmatch(r"[2-9][0-9]{11}", value):
            return "Aadhaar number must be 12 digits not starting with 0/1"
        if not verhoeff_ok(value):
            return "Verhoeff checksum failed"
    elif kind == "date":
        if re.fullmatch(r"(19|20)[0-9]{2}", value):
            return None
        if not re.fullmatch(r"[0-9]{2}/[0-9]{2}/[0-9]{4}", value):
            return "not a dd/mm/yyyy date"
        d, m, y = (int(x) for x in value.split("/"))
        if not (1 <= d <= 31 and 1 <= m <= 12 and 1900 <= y <= 2100):
            return "impossible date"
    return None


# ---- template loading / detection --------------------------------------------------

def load_templates():
    out = {}
    for p in sorted(glob.glob(os.path.join(TEMPLATE_DIR, "*.json"))):
        with open(p) as fh:
            t = json.load(fh)
        out[t["doc_type"]] = t
    return out


def detect_doc_type(lines, templates):
    """Score each template by keyword / pattern hits across all OCR text; None if nothing clears min_hits."""
    text = "\n".join(l.text for l in lines)
    scores = {}
    for name, t in templates.items():
        d = t["detect"]
        hits = sum(bool(re.search(k, text, re.I)) for k in d["keywords"])
        hits += sum(bool(re.search(p, text)) for p in d.get("pattern_hits", []))
        scores[name] = hits if hits >= d["min_hits"] else 0
    best = max(scores, key=scores.get) if scores else None
    return (best if scores.get(best) else None), scores


# ---- field strategies --------------------------------------------------------------

def _result(value=None, status="missing", reason="", conf=0.0, source="", absent=False):
    """absent=True: the thing is positively known not to be on this document (vs. merely not found)."""
    return {"value": value, "value_hi": "", "status": status, "reason": reason,
            "conf": round(conf, 3), "source": source, "absent": absent}


def _clean_value(f, raw):
    if f.get("fix") == "pan_positional":
        return fix_pan_positional(raw)
    if f.get("fix") == "digits_only":
        return re.sub(r"\D", "", raw)
    if f.get("fix") == "care_of":  # 'C/O: Name' -> 'C/O Name' (relation kept, OCR look-alikes repaired)
        m = re.match(r"(?i)([csdwh])\s*[/Il1|]\s*[oO0]\s*[:;.]\s*(.+)", raw)
        return f"{m.group(1).upper()}/O {m.group(2).strip()}" if m else raw.strip()
    if f.get("fix") == "aadhaar":
        if re.search(r"[xX*]", raw):  # masked card: keep the mask, XXXX XXXX 1234
            return "XXXX XXXX " + re.sub(r"\D", "", raw)[-4:]
        return re.sub(r"\D", "", raw)
    if f.get("fix") == "date":
        d = re.sub(r"\D", "", raw)
        if len(d) == 10 and d[2] == "1" and d[5] == "1":  # '/' misread as '1'
            d = d[:2] + d[3:5] + d[6:]
        if len(d) == 4:  # year of birth only
            return d
        return f"{d[:2]}/{d[2:4]}/{d[4:]}" if len(d) == 8 else raw.strip()
    return raw.strip()


def _finish(f, raw, conf, source):
    value = _clean_value(f, raw)
    if conf < f.get("min_conf", 0):
        return _result(None, "rejected", f"OCR confidence {conf:.2f} below {f['min_conf']}", conf, source)
    if "map" in f:
        value = f["map"].get(value.lower(), value)
    if f.get("validator"):
        err = validate(f["validator"], value)
        if err:
            return _result(value, "rejected", err, conf, source)
    return _result(value, "ok", "", conf, source)


def _below(lines, anchor_idx, n):
    a = lines[anchor_idx].box
    cands = [l for l in lines if l.box[1] > a[1] + 0.5 * (a[3] - a[1])
             and l.box[0] < a[2] and l.box[2] > a[0] - 10]  # same column
    cands.sort(key=lambda l: l.box[1])
    return cands[:n]


def _regex(f, lines, ctx):
    pat = re.compile(f["pattern"])
    pool = ctx["scoped"].get(f.get("scope"), lines) if f.get("scope") else lines
    if f.get("scope") and not pool:
        return _result(reason=f"no {f['scope']} block to search in")
    hits = []
    for l in pool:
        if f.get("exclude_line") and re.search(f["exclude_line"], l.text, re.I):
            continue
        for m in pat.finditer(l.text.upper() if f.get("fix") == "pan_positional" else l.text):
            hits.append((m.group(f.get("group", 0)), l))
    if f.get("near_anchor"):  # prefer a hit on / next to the label line
        anch = [l for l in lines if re.search(f["near_anchor"], l.text, re.I)]
        near = [h for h in hits if any(abs((h[1].box[1] + h[1].box[3]) - (a.box[1] + a.box[3])) < 2 * (a.box[3] - a.box[1]) for a in anch)]
        hits = near or hits
    if not hits:
        return _result(reason="pattern not found in any line")
    if f.get("prefer") == "most_frequent":
        counts = {}
        for raw, l in hits:
            counts.setdefault(_clean_value(f, raw), []).append(l)
        # a number repeated on front+back wins; valid checksum breaks ties
        best = max(counts, key=lambda v: (validate(f.get("validator"), v) is None, len(counts[v])))
        ls = counts[best]
        return _finish(f, best, max(l.conf for l in ls), ls[0].text)
    results = [_finish(f, raw, l.conf, l.text) for raw, l in hits]
    ok = [r for r in results if r["status"] == "ok"]
    return max(ok, key=lambda r: r["conf"]) if ok else max(results, key=lambda r: r["conf"])


def _anchor_idx(f, lines):
    return [i for i, l in enumerate(lines) if re.search(f["anchor"], l.text, re.I)]


def _below_anchor(f, lines, ctx):
    idxs = _anchor_idx(f, lines)
    if not idxs:
        return _result(reason="label not found (anchor " + f["anchor"] + ")")
    vp = re.compile(f["value_pattern"])
    rej = None
    for i in idxs:
        for l in _below(lines, i, f.get("max_lines_below", 2)):
            txt = re.sub(r"\s+", " ", l.text).strip(" _-|'\"")
            if vp.fullmatch(txt):
                r = _finish(f, txt, l.conf, l.text)
                if r["status"] == "ok":
                    r["line"] = l
                    return r
                rej = rej or r
    return rej or _result(reason="no line under the label matched the expected format")


def _above_anchor(f, lines, ctx):
    idxs = _anchor_idx(f, lines)
    if not idxs:
        return _result(reason="label not found (anchor " + f["anchor"] + ")")
    vp = re.compile(f["value_pattern"])
    rej = None
    for i in idxs:
        a = lines[i].box
        cands = [l for l in lines if l.box[3] <= a[1] + 0.5 * (a[3] - a[1])
                 and l.box[0] < a[2] and l.box[2] > a[0] - 10]
        cands.sort(key=lambda l: -l.box[3])  # nearest first
        for l in cands[:f.get("max_lines_above", 3)]:
            txt = re.sub(r"\s+", " ", l.text).strip(" _-|'\"")
            if vp.fullmatch(txt):
                r = _finish(f, txt, l.conf, l.text)
                if r["status"] == "ok":
                    r["line"] = l
                    return r
                rej = rej or r
    return rej or _result(reason="no line above the label matched the expected format")


def _block_from_anchor(f, lines, ctx):
    idxs = _anchor_idx(f, lines)
    cs = f.get("line_charset")  # an anchor in another script (Marathi 'S/O: ...') must not start the English block
    if cs:
        idxs = [i for i in idxs if len(re.findall(cs, lines[i].text)) >= 0.92 * len(lines[i].text)]
    if not idxs:
        return _result(reason="address label not found (card side without address?)")
    best = None
    for i in idxs:
        a = lines[i]
        block = [a]
        col = [l for l in lines if l.box[1] > a.box[1] and l.box[2] > a.box[0] and l.box[0] < a.box[0] + 1.5 * (a.box[2] - a.box[0])]
        for l in sorted(col, key=lambda l: l.box[1]):
            if re.search(f["stop"], l.text) or len(block) >= f["max_lines"]:
                break
            clean = len(re.findall(f.get("line_charset", "."), l.text)) >= 0.92 * len(l.text)
            if l.conf >= f.get("line_min_conf", 0) and clean:  # low-conf / symbol-heavy lines are other-script noise
                block.append(l)
        if best is None or len(block) > len(best):
            best = block
    ctx["scoped"]["address"] = best
    text = " ".join(l.text for l in best)  # raw block text, used for the pincode check
    text = re.sub(r"(?i)^\W*[a-z]?address\W*", "", text).strip()
    conf = sum(l.conf for l in best) / len(best)
    if len(best) < 2:
        return _result(None, "rejected", "address block too short to trust", conf, text)
    if f.get("require") and not re.search(f["require"], text):
        return _result(None, "rejected", "address block has no pincode - likely misread", conf, text)
    if conf < f["min_conf"]:
        return _result(None, "rejected", f"mean OCR confidence {conf:.2f} below {f['min_conf']}", conf, text)
    parts = [re.sub(r"(?i)^\W*[a-z]?address\W*", "", l.text) for l in best]
    return _result(clean_pin_and_care_of(parts), "ok", "", conf, text)  # one string, no 'S/O ..' lead-in, no PIN


def _boxes_cover(lines, region):
    """Fraction of the region covered by OCR text boxes."""
    x0, y0, x1, y1 = region
    area = max(1, (x1 - x0) * (y1 - y0))
    cov = 0
    for l in lines:
        ix = max(0, min(x1, l.box[2]) - max(x0, l.box[0]))
        iy = max(0, min(y1, l.box[3]) - max(y0, l.box[1]))
        cov += ix * iy
    return cov / area


def _first(lines, pattern):
    return next((l for l in lines if re.search(pattern, l.text, re.I)), None)


def _photo_region(lines, doc_type, h, w):
    """Where the portrait should be, from printed anchors (not fixed pixels). None if anchors are missing."""
    if doc_type == "pan":
        title = _first(lines, r"permanent\s*account|number\s*card")
        head = _first(lines, r"income\s*tax")
        nm = next((l for l in lines if re.search(r"\bname\b", l.text, re.I) and not re.search("father", l.text, re.I)), None)
        if title and nm:
            top = head.box[3] if head else 0
            return (0, top, max(10, title.box[0] - 8), max(top + 20, nm.box[1]))
    else:
        fr = [l for l in lines if re.search(r"\bdob\b|birth|\b(fe)?male\b|year of", l.text, re.I)]
        if fr:
            gov = _first(lines, r"government\s*of\s*india")
            num = [l for l in lines if re.fullmatch(r"[\dXx*][\dXx* ._,]{9,}", l.text.strip())]
            x1 = min(l.box[0] for l in fr) - 6
            top = gov.box[3] if gov else 0
            bottom = min((l.box[1] for l in num if l.box[1] > top), default=h)
            return (0, top, max(10, x1), max(top + 20, bottom))
    return None


def _looks_like_photo(img, region, lines):
    x0, y0, x1, y1 = (int(v) for v in region)
    x0, y0 = max(0, x0), max(0, y0)
    crop = img[y0:y1, x0:x1]
    if crop.shape[0] < 30 or crop.shape[1] < 30:
        return False, "portrait area too small"
    if _boxes_cover(lines, (x0, y0, x1, y1)) > 0.25:
        return False, "text found where the portrait should be"
    gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
    if float(gray.std()) < 22:
        return False, "portrait area is blank"
    edge = float((cv2.Canny(gray, 50, 120) > 0).mean())
    return (0.01 < edge < 0.4), (None if 0.01 < edge < 0.4 else f"portrait area has no photo-like texture (edge density {edge:.3f})")


def _qr_blob(img, lines):
    """Fallback when no QR decoder fires: a square, bi-level (black/white only) block with fine structure."""
    g = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    s = 700 / max(g.shape)
    g = cv2.resize(g, None, fx=s, fy=s, interpolation=cv2.INTER_AREA) if s < 1 else g
    grad = cv2.morphologyEx(g, cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8))
    m = cv2.morphologyEx((grad > 60).astype(np.uint8), cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    n, _, st, _ = cv2.connectedComponentsWithStats(m)
    total = g.shape[0] * g.shape[1]
    for i in range(1, n):
        x, y, bw, bh, _ = st[i]
        if not (0.012 * total < bw * bh < 0.35 * total and 0.75 < bw / bh < 1.33):
            continue
        roi = g[y:y + bh, x:x + bw]
        bilevel = float(((roi < 90) | (roi > 170)).mean())
        dark = float((roi < 110).mean())
        if bilevel > 0.9 and 0.25 < dark < 0.75:
            return True
    return False


def _image_field(f, lines, ctx):
    """Presence checks for non-text parts (photo / QR / signature). The value is always empty."""
    img = ctx.get("img")
    if img is None:
        return _result(reason="no image supplied")
    kind = f["detector"]
    h, w = img.shape[:2]
    if kind == "face":
        region = _photo_region(lines, ctx["doc_type"], h, w)
        if region is None:
            return _result(reason="could not locate the portrait area (anchor text not read)")
        ok, why = _looks_like_photo(img, region, lines)
        return _result("", "ok") if ok else _result(reason=why)
    if kind == "qr":
        for mk in (cv2.QRCodeDetector, cv2.QRCodeDetectorAruco):  # detectors disagree on photographed cards
            for sc in (1, 2):
                try:
                    if mk().detect(cv2.resize(img, None, fx=sc, fy=sc) if sc > 1 else img)[0]:
                        return _result("", "ok")
                except cv2.error:
                    pass
        return _result("", "ok") if _qr_blob(img, lines) else _result(reason="QR code was not detected.")
    if kind == "signature":
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        label = [l for l in lines if re.search(f["label"], l.text, re.I)]
        if not label:
            return _result(reason="no signature label on this card", absent=True)
        x0, y0, x1, y1 = label[0].box
        lh = y1 - y0
        roi = gray[max(0, y0 - 4 * lh):max(0, y0 - lh // 4), max(0, x0 - lh):x1 + 2 * lh]
        if roi.size == 0:
            return _result(None, "rejected", "signature area outside the image")
        density = float((cv2.Canny(roi, 60, 160) > 0).mean())
        if density < 0.02:
            return _result(None, "rejected", f"label found but no ink in the signature area (edge density {density:.3f})")
        return _result("", "ok")
    return _result(reason="unknown detector")


STRATEGIES = {"regex": _regex, "below_anchor": _below_anchor, "above_anchor": _above_anchor,
              "block_from_anchor": _block_from_anchor, "image": _image_field}


def extract(lines, templates=None, doc_type=None, img=None):
    templates = templates or load_templates()
    detected, scores = detect_doc_type(lines, templates)
    doc_type = doc_type or detected
    if doc_type is None:
        return {"doc_type": None, "scores": scores, "fields": {},
                "error": "Image does not match any card template - nothing extracted."}
    ctx = {"scoped": {}, "img": img, "doc_type": doc_type}
    fields = {}
    for f in templates[doc_type]["fields"]:
        fields[f["name"]] = STRATEGIES[f["strategy"]](f, lines, ctx)
    side = _aadhaar_side(lines, fields) if doc_type == "aadhaar" else None
    return {"doc_type": doc_type, "scores": scores, "fields": fields, "side": side, "error": None,
            "matched": scores.get(doc_type, 0) > 0, "nlines": len(lines)}


def _aadhaar_side(lines, fields):
    """front / back / both / unknown, from what is actually printed on the page."""
    text = "\n".join(l.text for l in lines)
    front = any(fields[k]["status"] == "ok" for k in ("name", "dob_or_yob", "gender") if k in fields) \
        or bool(re.search(r"\bdob\b|date\s*of\s*birth|year\s*of\s*birth", text, re.I))
    back = any(fields[k]["status"] == "ok" for k in ("address", "pincode", "enrolment_number", "care_of") if k in fields) \
        or bool(re.search(r"^\W*[a-z]?address\b|unique\s*identification|enrol", text, re.I | re.M))
    return "both" if front and back else "front" if front else "back" if back else "unknown"
