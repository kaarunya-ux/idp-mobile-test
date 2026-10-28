"""Side-by-side table of every benchmarked model (bench/results/*.json).

    python bench/compare.py              prints the table
    python bench/compare.py --md out.md  also writes it as Markdown

For each model and concurrency level: median and 95th-percentile latency, throughput, field accuracy,
errors. "Best N" per model = the highest level whose median latency stays within --max-p50 seconds,
with no errors and accuracy within 2 points of that model's single-user accuracy.
"""

import argparse
import glob
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))


def fmt_s(ms):
    return f"{ms / 1000:.1f}" if ms else "-"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--max-p50", type=float, default=10, help="latency budget per request, seconds")
    ap.add_argument("--md", help="also write the table to this Markdown file")
    args = ap.parse_args()

    results = []
    for path in sorted(glob.glob(os.path.join(HERE, "results", "*.json"))):
        with open(path) as f:
            results.append(json.load(f))
    if not results:
        raise SystemExit("No results yet: run bench/bench.py first.")

    lines = ["| Model | N | OK | p50 s | p95 s | Docs/min | Accuracy | Errors |",
             "|---|---|---|---|---|---|---|---|"]
    best = []
    for r in results:
        base = next((lv["accuracy"] for lv in r["levels"] if lv["accuracy"] is not None), None)
        good = None
        for lv in r["levels"]:
            err = ", ".join(f"{k} {v}" for k, v in lv["errors"].items()) or "-"
            acc = f"{lv['accuracy'] * 100:.1f}%" if lv["accuracy"] is not None else "-"
            lines.append(f"| {r['label']} | {lv['level']} | {lv['ok']}/{lv['requests']} | {fmt_s(lv['p50_ms'])} | "
                         f"{fmt_s(lv['p95_ms'])} | {lv['throughput_per_min']} | {acc} | {err} |")
            if (not lv["errors"] and lv["p50_ms"] and lv["p50_ms"] <= args.max_p50 * 1000
                    and base is not None and lv["accuracy"] >= base - 0.02):
                good = lv
        best.append((r["label"], good))

    lines += ["", f"Best concurrency per model (median <= {args.max_p50:g} s, no errors, accuracy kept):", ""]
    for label, lv in best:
        lines.append(f"- **{label}**: " + (f"N={lv['level']} ({fmt_s(lv['p50_ms'])} s median, "
                                          f"{lv['throughput_per_min']} docs/min, {lv['accuracy'] * 100:.1f}%)"
                                          if lv else "no level met the budget"))
    text = "\n".join(lines)
    print(text)
    if args.md:
        with open(args.md, "w") as f:
            f.write(text + "\n")


if __name__ == "__main__":
    main()
