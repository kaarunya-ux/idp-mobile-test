"""Concurrency benchmark for the IDP web app: how many users can one model serve at once?

    python bench/bench.py --levels 1,2,4,8 --rounds 2

Sends the ground-truth images (bench/ground_truth.json) to the running app at --api, N requests at a
time for each level N, and records per request: latency, the model's read/write timings, errors and
which fields came back correct. Results go to bench/results/<model>.json (no extracted values, only
right/wrong per field); bench/compare.py puts all models side by side.

The model host must run with at least as many slots as the highest level (NP in
model_host/start_model.sh); extra requests queue on the host and show up as higher latency.
"""

import argparse
import asyncio
import difflib
import json
import os
import re
import statistics
import time

import io
import random

import httpx
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
ADDRESS_MATCH = 0.9   # addresses are long; count as correct at >= 90% character similarity


def norm(v) -> str:
    return re.sub(r"[^a-z0-9]", "", str(v).lower()) if v is not None else ""


def field_ok(key: str, expected, got) -> tuple[bool, float | None]:
    if expected is None:
        return got is None, None
    if got is None:
        return False, 0.0 if key == "address" else None
    if key == "address":
        ratio = difflib.SequenceMatcher(None, norm(expected), norm(got)).ratio()
        return ratio >= ADDRESS_MATCH, round(ratio, 3)
    return norm(expected) == norm(got), None


_sent = random.randrange(342)   # random start, so a new run does not repeat the last run's images


def unique(raw: bytes) -> bytes:
    """The image with its red, green and blue levels each shifted by -3..+3 (out of 255), a different
    combination for every request (342 before any repeat). Invisible to the model, but each request then
    carries an image the model host has never seen, as with real users, so its cache can reuse only the
    prompt text, never the image."""
    global _sent
    _sent += 1
    n = _sent % 342 + 1                                  # 1..342: never all three shifts zero
    shifts = (n % 7 - 3, n // 7 % 7 - 3, n // 49 % 7 - 3)
    channels = Image.open(io.BytesIO(raw)).convert("RGB").split()
    img = Image.merge("RGB", [c.point(lambda v, d=d: min(255, max(0, v + d))) for c, d in zip(channels, shifts)])
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


async def one(client: httpx.AsyncClient, api: str, case: dict, images: dict) -> dict:
    files = {}
    for key in ("file", "back"):
        if case.get(key):
            name, raw = images[case[key]]
            files[key] = (name + ".png", unique(raw))
    t0 = time.perf_counter()
    try:
        r = await client.post(f"{api}/api/v1/{case['doc_type']}/extract?debug=true", files=files)
        body = r.json()
    except httpx.TimeoutException:
        return {"id": case["id"], "status": "client_timeout", "ms": round((time.perf_counter() - t0) * 1000)}
    except (httpx.HTTPError, ValueError) as e:
        return {"id": case["id"], "status": f"client_error:{type(e).__name__}",
                "ms": round((time.perf_counter() - t0) * 1000)}
    ms = round((time.perf_counter() - t0) * 1000)
    if r.status_code != 200:
        return {"id": case["id"], "status": body.get("error", {}).get("code", f"HTTP_{r.status_code}"), "ms": ms}
    t = body.get("meta", {}).get("timings", {})
    wrong, address_ratio = [], None
    for key, expected in case["fields"].items():
        ok, ratio = field_ok(key, expected, body.get(key))
        if ratio is not None:
            address_ratio = ratio
        if not ok:
            wrong.append(key)
    return {"id": case["id"], "status": "ok", "ms": ms, "fields": len(case["fields"]), "wrong": wrong,
            "address_ratio": address_ratio, "prompt_tokens": t.get("prompt_tokens"),
            "prompt_ms": t.get("prompt_ms"), "output_tokens": t.get("output_tokens"), "output_ms": t.get("output_ms")}


def pct(values: list, q: float):
    if not values:
        return None
    values = sorted(values)
    return values[min(len(values) - 1, round(q * (len(values) - 1)))]


def summarise(level: int, rows: list, wall_s: float) -> dict:
    ok = [r for r in rows if r["status"] == "ok"]
    lat = [r["ms"] for r in ok]
    fields = sum(r["fields"] for r in ok)
    correct = fields - sum(len(r["wrong"]) for r in ok)
    errors = {}
    for r in rows:
        if r["status"] != "ok":
            errors[r["status"]] = errors.get(r["status"], 0) + 1
    return {
        "level": level, "requests": len(rows), "ok": len(ok), "errors": errors,
        "p50_ms": pct(lat, 0.5), "p95_ms": pct(lat, 0.95), "max_ms": max(lat) if lat else None,
        "wall_s": round(wall_s, 1), "throughput_per_min": round(len(ok) / wall_s * 60, 1) if wall_s else None,
        "accuracy": round(correct / fields, 3) if fields else None,
        "docs_all_correct": sum(1 for r in ok if not r["wrong"]),
        "avg_prompt_ms": round(statistics.mean(r["prompt_ms"] for r in ok)) if ok else None,
        "avg_output_ms": round(statistics.mean(r["output_ms"] for r in ok)) if ok else None,
    }


async def run_level(client, api, cases, images, level: int, rounds: int) -> tuple[list, float]:
    # Enough requests that all N slots stay busy, at least every case `rounds` times.
    n = max(len(cases), level) * rounds
    jobs = [cases[i % len(cases)] for i in range(n)]
    sem = asyncio.Semaphore(level)

    async def gated(case):
        async with sem:
            return await one(client, api, case, images)

    t0 = time.perf_counter()
    rows = await asyncio.gather(*(gated(c) for c in jobs))
    return list(rows), time.perf_counter() - t0


def print_level(s: dict):
    err = ", ".join(f"{k}={v}" for k, v in s["errors"].items()) or "none"
    acc = f"{s['accuracy'] * 100:.1f}%" if s["accuracy"] is not None else "-"
    p50 = f"{s['p50_ms'] / 1000:.1f}s" if s["p50_ms"] else "-"
    p95 = f"{s['p95_ms'] / 1000:.1f}s" if s["p95_ms"] else "-"
    print(f"  N={s['level']:<3} ok {s['ok']}/{s['requests']}  p50 {p50}  p95 {p95}  "
          f"{s['throughput_per_min']}/min  accuracy {acc}  errors: {err}", flush=True)


async def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--api", default="http://127.0.0.1:8020", help="the running IDP web app")
    ap.add_argument("--levels", default="1,2,4,8", help="concurrent requests to test, comma-separated")
    ap.add_argument("--rounds", type=int, default=2, help="times each image is sent per level")
    ap.add_argument("--docs", default="pan,aadhaar,dl", help="document types to include")
    ap.add_argument("--label", help="name for the results file (default: model id from /health)")
    ap.add_argument("--gt", default=os.path.join(HERE, "ground_truth.json"))
    ap.add_argument("--timeout", type=float, default=180, help="client timeout per request, seconds")
    args = ap.parse_args()

    with open(args.gt) as f:
        cases = [c for c in json.load(f)["cases"] if c["doc_type"] in args.docs.split(",")]
    if not cases:
        raise SystemExit("No test cases for those document types in the ground truth.")
    images = {}
    for c in cases:
        for key in ("file", "back"):
            if c.get(key) and c[key] not in images:
                with open(os.path.join(REPO, c[key]), "rb") as f:
                    images[c[key]] = (os.path.basename(c[key]), f.read())
    levels = [int(x) for x in args.levels.split(",")]

    limits = httpx.Limits(max_connections=max(levels) + 2)
    async with httpx.AsyncClient(timeout=args.timeout, limits=limits) as client:
        health = (await client.get(f"{args.api}/health")).json()
        if health.get("status") != "ok":
            raise SystemExit(f"App or model host not ready: {health}")
        label = args.label or health.get("model") or "model"
        print(f"Model {health.get('model')} via {args.api}; {len(cases)} test images; levels {levels}", flush=True)

        print("Warm-up request...", flush=True)
        await one(client, args.api, cases[0], images)

        result = {"model": health.get("model"), "label": label, "api": args.api, "rounds": args.rounds,
                  "started": time.strftime("%Y-%m-%d %H:%M:%S"), "levels": []}
        for level in levels:
            rows, wall = await run_level(client, args.api, cases, images, level, args.rounds)
            s = summarise(level, rows, wall)
            print_level(s)
            result["levels"].append({**s, "rows": rows})

    out_dir = os.path.join(HERE, "results")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, re.sub(r"[^A-Za-z0-9._-]", "_", label) + ".json")
    with open(path, "w") as f:
        json.dump(result, f, indent=1)
    print(f"Saved {path}")


if __name__ == "__main__":
    asyncio.run(main())
