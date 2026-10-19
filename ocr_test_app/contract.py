"""Internal extraction result -> the JSON contract of the OCR Template Lab frontend (see README).

Every field: {value_en, value_hi, status, confidence?, reason}. Strings only, never null; confidence is left out
when unknown. Text is NFC-normalised so Devanagari compares equal on the other side.
"""
import re
import unicodedata

VISUAL = {"photo", "signature", "qr_code"}
SIDE_LABEL = {"front": "front", "back": "back"}


def human_reason(reason):
    """Short, user-facing wording instead of the extractor's internal diagnostics."""
    r = (reason or "").lower()
    for key, msg in (("checksum", "Aadhaar checksum invalid"), ("valid pan", "Not a valid PAN number"),
                     ("impossible date", "Date is not valid"), ("dd/mm/yyyy", "Date is not valid"),
                     ("pincode", "Address could not be read reliably (no PIN code found)"),
                     ("portrait", "Photo not detected"), ("qr", "QR code not detected"),
                     ("signature", "Signature not detected"), ("confidence", "Text could not be read reliably")):
        if key in r:
            return msg
    return "Could not read this field reliably"


def _group_aadhaar(v):
    d = re.sub(r"\D", "", v)
    return f"{d[:4]} {d[4:8]} {d[8:]}" if len(d) == 12 and not v.startswith("X") else v


def field_spec(name, meta, r, doc_type, side):
    en, hi = (r.get("value") or ""), (r.get("value_hi") or "")
    if r["status"] == "ok" and (en or hi or name in VISUAL):
        if name in VISUAL:
            en = "Detected"
        elif name == "aadhaar_number":
            en = _group_aadhaar(en)
        out = {"value_en": unicodedata.normalize("NFC", en), "value_hi": unicodedata.normalize("NFC", hi),
               "status": "extracted", "reason": ""}
        conf = r.get("conf") or r.get("conf_hi")
        if conf and name not in VISUAL:
            out["confidence"] = conf
        return out
    if doc_type == "aadhaar" and side in SIDE_LABEL and meta.get("side", "both") not in (side, "both"):
        return _fail("not_available", f"{SIDE_LABEL[meta['side']].capitalize()} side not provided")
    if r.get("absent"):
        return _fail("not_available", "Not printed on this card")
    if r["status"] == "missing" and not meta.get("required", True):
        return _fail("not_available", "Older PAN without QR" if (doc_type, name) == ("pan", "qr_code")
                     else "Not printed on this card")
    return _fail("failed", human_reason(r.get("reason")))


def _fail(status, reason):
    return {"value_en": "", "value_hi": "", "status": status, "reason": reason}


def build_response(res, template, engine, ms, languages):
    if res["doc_type"] is None:
        return None  # caller turns this into an error response
    meta = {f["name"]: f for f in template["fields"]}
    fields = {n: field_spec(n, meta[n], r, res["doc_type"], res.get("side")) for n, r in res["fields"].items()}
    out = {"success": True, "template_type": res["doc_type"], "engine": engine, "languages": languages}
    if res["doc_type"] == "aadhaar":
        out["document_side_detected"] = res["side"]
    out["fields"] = fields
    out["processing_time_ms"] = ms
    return out
