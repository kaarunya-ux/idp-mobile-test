"""Score test_cards.py runs against ground_truth.json and compare modes side by side.

    .venv/bin/python visiting_card_api/score.py crop 1600 1280 1024

Matching: name and email ignore case and spacing; phone compares digits only; address ignores
case and punctuation and passes at >= 90% similarity. A null in the ground truth must come back null.
"""

import difflib
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIELDS = ("name", "email", "phone", "address")
truth = {k: v for k, v in json.loads((HERE / "ground_truth.json").read_text()).items()
         if not k.startswith("_") and "_skip" not in v}


def norm(field, v):
    v = v.replace("ی", "ي").replace("ى", "ي")    # Persian/Arabic yeh look identical on the card
    if field == "phone":
        return re.sub(r"\D", "", v).lstrip("0")    # "001 (647)..." and "+1 (647)..." are the same number
    if field == "address":
        return re.sub(r"[\W_]+", "", v.casefold())
    return " ".join(v.casefold().split())


def correct(field, got, want) -> bool:
    if want is None:
        return got is None
    if got is None:
        return False
    for w in (want if isinstance(want, list) else [want]):
        a, b = norm(field, got), norm(field, w)
        if a == b or (field == "address" and difflib.SequenceMatcher(None, a, b).ratio() >= 0.9):
            return True
    return False


modes = sys.argv[1:] or ["crop", "1600", "1280", "1024"]
runs = {}
for m in modes:
    p = HERE / "logs" / f"test_{m}.json"
    if p.exists():
        runs[m] = {r["file"]: r["response"] for r in json.loads(p.read_text())}

summary = {}
misses = []
for m, res in runs.items():
    right = total = 0
    times = []
    for f, want in truth.items():
        r = res.get(f)
        if not r or not r.get("success"):
            total += 4
            misses.append((m, f, "ERROR", str(r and r.get("error")), ""))
            continue
        times.append(r["meta"]["processing_ms"] / 1000)
        got = dict(r["data"])
        if got.get("country_code") and got.get("phone"):   # runs since the split: score the full number
            got["phone"] = got["country_code"] + " " + got["phone"]
        for k in FIELDS:
            total += 1
            if correct(k, got[k], want[k]):
                right += 1
            else:
                misses.append((m, f, k, r["data"][k], want[k]))
    t = sorted(times)
    summary[m] = (right, total, sum(t) / len(t) if t else 0, t[len(t) // 2] if t else 0, t[-1] if t else 0)

print(f"{'mode':6} {'fields':>8} {'avg s':>6} {'median':>7} {'max':>6}")
for m, (right, total, avg, med, mx) in summary.items():
    print(f"{m:6} {right:>3}/{total:<4} {avg:6.1f} {med:7.1f} {mx:6.1f}")
print("\nMisses:")
for m, f, k, got, want in misses:
    print(f"  [{m}] {f} {k}: got {got!r}  expected {want!r}")
