"""Devanagari (Hindi / Marathi) side of the Aadhaar fields.

The English fields come from extractor.py. Aadhaar prints the same facts in two scripts on separate lines, so the
Hindi values are found from the lines that are mostly Devanagari: the line above the English name, the gender word,
the "S/O ..." relation and the block under the Hindi "पता:" label. Only text that was actually read is returned -
nothing is transliterated or translated. Needs a recogniser that knows Devanagari (engine ppocrv5); with the others
these lines decode as noise, fail the checks below and the Hindi values stay empty.
"""
import re
import unicodedata

DEV = re.compile(r"[ऀ-ॿ]")
DEV_RUN = r"[ऀ-ॿ]"
SEP = r"[\s:;/|.\-]"
# printed headings / labels that are not a person's name
HEADER = {"भारत", "सरकार", "आधार", "भारतीय", "विशिष्ट", "प्राधिकरण", "अधिकार", "सत्यमेव", "जयते", "जन्म", "तारीख",
          "तिथि", "पुरुष", "महिला", "स्त्री", "पता", "पत्ता", "माणसाचा", "सामान्य", "मेरा", "पहचान", "आम", "आदमी",
          "का", "अळख", "ओळख", "नाम", "नाव", "पहचानपत्र", "पहचान", "प्राधिकरण", "आधार"}
GENDER_HI = [(re.compile("पुरुष"), "पुरुष"), (re.compile("महिला|स्त्री"), "महिला"),
             (re.compile("ट्रांसजेंडर|तृतीय"), "ट्रांसजेंडर")]
REL_HI = "आत्मजा|आत्मज|पत्नी|पति|द्वारा|पुत्री|पुत्र"
REL_ANY = rf"(?:{REL_HI})|[CSDWHcsdwh]\s*[/Il1|]\s*[Oo0]"
STOP = re.compile(r"(?i)\bvid\b|1947|uidai|help|www|gov|mobile|[0-9]{4}[\s._,-]?[0-9]{4}[\s._,-]?[0-9]{4}|[xX*]{4}[\s._,-]?[xX*]{4}|enrol|your\s*aadhaar")
PIN_TAIL = re.compile(r"[,\s\-–.]*(?:PIN\s*Code\s*[:\-]?\s*)?(?<![0-9०-९])(?:[1-9][0-9]{5}|[१-९][०-९]{5})(?![0-9०-९])[,\s.]*$", re.I)
CARE_HEAD = re.compile(rf"^\W*(?:{REL_ANY})\s*[:;.]?\s*[^,]*(?:,|$)\s*")


def nfc(s):
    return unicodedata.normalize("NFC", s)


def dev_ratio(text):
    d = len(DEV.findall(text))
    lat = len(re.findall(r"[A-Za-z]", text))
    return d / max(1, d + lat)


def clean_pin_and_care_of(lines_text):
    """Join address lines into one string: drop the 'S/O <name>' segment of the first line and the trailing PIN."""
    parts = [t.strip() for t in lines_text if t.strip()]
    if parts:
        parts[0] = CARE_HEAD.sub("", parts[0], count=1).strip()
    text = re.sub(r"\s+", " ", " ".join(p for p in parts if p)).strip(" ,;-")
    return PIN_TAIL.sub("", text).strip(" ,;-")


def _above(lines, ref, max_h=3.0):
    h = max(1, ref.box[3] - ref.box[1])
    cand = [l for l in lines if l is not ref and l.box[3] <= ref.box[1] + 0.5 * h
            and ref.box[3] - l.box[3] < (max_h + 1) * h and l.box[0] < ref.box[2] and l.box[2] > ref.box[0] - 10]
    return sorted(cand, key=lambda l: -l.box[3])


def hindi_name(lines, en_line):
    """Nearest mostly-Devanagari line above the English name line that is not a heading."""
    for l in _above(lines, en_line)[:3]:
        t = re.sub(rf"^{SEP}*(नाम|नाव){SEP}*", "", nfc(l.text)).strip(" :|/-.")
        if l.conf >= 0.6 and dev_ratio(l.text) >= 0.7 and len(DEV.findall(t)) >= 3 \
                and not (set(re.findall(rf"{DEV_RUN}+", t)) & HEADER):
            return t, l.conf
    return None


def hindi_gender(lines):
    for l in lines:
        for rx, val in GENDER_HI:
            if l.conf >= 0.6 and rx.search(nfc(l.text)):
                return val, l.conf
    return None


def hindi_care_of(lines):
    rx = re.compile(rf"({REL_ANY})\s*[:;.]?\s*({DEV_RUN}[ऀ-ॿ ]*{DEV_RUN})")
    for l in lines:
        m = rx.search(nfc(l.text)) if DEV.search(l.text) else None
        if m and l.conf >= 0.7:
            label = m.group(1)
            if re.fullmatch(r"[CSDWHcsdwh]\s*[/Il1|]\s*[Oo0]", label):
                label = label[0].upper() + "/O"
            return f"{label}: {m.group(2).strip()}", l.conf
    return None


def hindi_address(lines):
    """Block under the Hindi 'पता:' label, same column, up to the first English / footer line. PIN is removed."""
    anchors = [l for l in lines if re.match(rf"^{SEP}*(पता|पत्ता)", nfc(l.text))]
    best = None
    for a in anchors:
        aw = a.box[2] - a.box[0]
        block = []
        col = [l for l in lines if l.box[1] > a.box[1] + 0.3 * (a.box[3] - a.box[1]) and l.box[2] > a.box[0]
               and l.box[0] < a.box[0] + max(1.5 * aw, 60)]
        for l in sorted(col, key=lambda l: l.box[1]):
            if STOP.search(l.text) or len(block) >= 8:
                break
            letters = len(re.findall(r"[A-Za-z]", l.text))
            if dev_ratio(l.text) < 0.25 and letters >= 5:   # English panel starts
                break
            if l.conf >= 0.7:
                block.append(l)
        if best is None or len(block) > len(best):
            best = block
    if not best:
        return None
    first_rest = re.sub(rf"^{SEP}*(पता|पत्ता){SEP}*", "", nfc(best[0].text))
    texts = [first_rest if first_rest.strip() else ""] + [nfc(l.text) for l in best[1:]]
    text = clean_pin_and_care_of(texts)
    if len(DEV.findall(text)) < 6:
        return None
    return text, sum(l.conf for l in best) / len(best)


def add_hindi(res, lines):
    """Fill value_hi (and flip a failed English field to ok when only the Hindi was read) on an Aadhaar result."""
    if res.get("doc_type") != "aadhaar":
        return res
    f = res["fields"]
    found = {}
    nm = f["name"].get("line") if "name" in f else None
    if nm is not None and (h := hindi_name(lines, nm)):
        found["name"] = h
    if "gender" in f and (h := hindi_gender(lines)):
        found["gender"] = h
    if "care_of" in f and (h := hindi_care_of(lines)):
        found["care_of"] = h
    if "address" in f and (h := hindi_address(lines)):
        found["address"] = h
    for k, (text, conf) in found.items():
        f[k]["value_hi"], f[k]["conf_hi"] = text, round(conf, 3)
        if f[k]["status"] != "ok":
            f[k]["status"], f[k]["reason"] = "ok", ""
    return res
