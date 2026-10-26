"""Document -> OCR -> template extraction, with orientation / geometry fallbacks.

For each page the first attempt is the card-cropped, deskewed image. If that does not yield a
confident read (template found and >= 3 text fields accepted) the other candidates are tried
(original image, then 90/180/270 degree turns) and the best-scoring attempt wins. At most
MAX_PASSES OCR passes are spent per page.
"""
import os
import time

import numpy as np

from extractor import extract, load_templates
from hindi import add_hindi
from preprocess import prepare, rotate

MAX_PASSES = 5
TIME_BUDGET_S = float(os.environ.get("OCR_TIME_BUDGET_S", 60))  # per page; no new pass after this
GOOD_TEXT_FIELDS = 3
VISUAL = {"photo", "signature", "qr_code"}


def text_ok(res):
    return sum(1 for k, v in res["fields"].items() if k not in VISUAL and v["status"] == "ok")


def _score(res):
    return 0 if res["doc_type"] is None else 1 + text_ok(res)


def _attempts(img):
    base, note = prepare(img)
    yield "prepared", note, base
    if base is not img:
        yield "original", "as uploaded", img
    for k, name in ((1, "90"), (3, "270"), (2, "180")):
        yield f"rot{name}", f"rotated {name}deg", np.rot90(base, k).copy()


def read_page(img, ocr, forced, log=lambda m: None):
    """Best (lines, result, image, label) for one page."""
    templates = load_templates()
    best = None
    t0 = time.time()
    for n, (label, note, cand) in enumerate(_attempts(img)):
        if n >= MAX_PASSES or (n and time.time() - t0 > TIME_BUDGET_S):
            break
        if n and best is not None and len(best[0]) == 0:
            log("stopping: no text detected at all, retrying other angles will not help")
            break
        lines = ocr.read(cand)
        res = add_hindi(extract(lines, templates, doc_type=forced, img=cand), lines)
        log(f"pass {n + 1} [{label}: {note}] lines={len(lines)} template={res['doc_type']} text_fields_ok={text_ok(res)}")
        if best is None or _score(res) > _score(best[1]):
            best = (lines, res, cand, label)
        if res["doc_type"] and text_ok(res) >= GOOD_TEXT_FIELDS:
            break
    return best


RANK = {"ok": 2, "rejected": 1, "missing": 0}


def merge_pages(results):
    """Several pages (e.g. Aadhaar front and back on separate pages): per field keep the best outcome."""
    results = [r for r in results if r["doc_type"]]
    if not results:
        return None
    merged = dict(results[0])
    merged["fields"] = dict(results[0]["fields"])
    for r in results[1:]:
        for k, v in r["fields"].items():
            cur = merged["fields"].get(k)
            if cur is None or (RANK[v["status"]], v["conf"]) > (RANK[cur["status"]], cur["conf"]):
                merged["fields"][k] = v
    merged["matched"] = any(r.get("matched") for r in results)
    merged["nlines"] = sum(r.get("nlines", 0) for r in results)
    sides = {r.get("side") for r in results if r.get("side")} - {"unknown"}
    if merged["doc_type"] == "aadhaar":
        merged["side"] = "both" if "both" in sides or {"front", "back"} <= sides else (sides.pop() if len(sides) == 1 else "unknown")
    return merged


def process(pages, ocr, template_type, log=lambda m: None):
    """pages: list of RGB arrays. Returns (merged internal result or None, elapsed ms)."""
    t = time.time()
    results = []
    for i, page in enumerate(pages):
        lines, res, _, label = read_page(page, ocr, template_type, log)
        results.append(res)
    return merge_pages(results), int((time.time() - t) * 1000)
