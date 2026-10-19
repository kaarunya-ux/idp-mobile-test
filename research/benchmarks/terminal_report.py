#!/usr/bin/env python3
"""
Terminal accuracy + latency report for the box-vs-crop VLM benchmark.

Usage:
  python3 terminal_report.py <label1>:<results1.json> <label2>:<results2.json>

Example:
  python3 terminal_report.py \\
      Nano:/home/kaarunya/Downloads/model_testing/aadhaar_bench_results/nano_results.json \\
      Flash:/home/kaarunya/Downloads/model_testing/aadhaar_bench_results/flash_results.json

Each results.json entry must have: name, field, variant, case ("box"/"crop"),
ground_truth, output, latency_s, correct (bool).
"""
import json, sys

RESET = "\033[0m"
BOLD = "\033[1m"
GREEN = "\033[32m"
RED = "\033[31m"
CYAN = "\033[36m"
YELLOW = "\033[33m"
DIM = "\033[2m"

def load(path):
    with open(path) as f:
        return json.load(f)

def pct(n, d):
    return f"{100*n/d:.1f}%" if d else "n/a"

def avg(vals):
    return sum(vals) / len(vals) if vals else 0.0

def verdict_str(correct):
    if correct is None:
        return f"{YELLOW}PENDING{RESET}"
    return f"{GREEN}PASS{RESET}" if correct else f"{RED}FAIL{RESET}"

def print_table(label, results):
    print(f"\n{BOLD}{CYAN}== {label} — per-case results =={RESET}")
    hdr = f"{'card':<10} {'field':<15} {'variant':<15} {'latency':>8}  verdict"
    print(hdr)
    print(DIM + "-" * len(hdr) + RESET)
    for r in results:
        lat = f"{r['latency_s']:.1f}s"
        print(f"{r['name']:<10} {r['field']:<15} {r['variant']:<15} {lat:>8}  {verdict_str(r.get('correct'))}")

def summarize(label, results):
    box = [r for r in results if r["case"] == "box"]
    crop = [r for r in results if r["case"] == "crop"]
    box_scored = [r for r in box if r.get("correct") is not None]
    crop_scored = [r for r in crop if r.get("correct") is not None]
    all_scored = box_scored + crop_scored
    pending = len(results) - len(all_scored)

    box_correct = sum(1 for r in box_scored if r["correct"])
    crop_correct = sum(1 for r in crop_scored if r["correct"])
    total_correct = box_correct + crop_correct

    box_lat = avg([r["latency_s"] for r in box if r["latency_s"] > 0])
    crop_lat = avg([r["latency_s"] for r in crop if r["latency_s"] > 0])
    overall_lat = avg([r["latency_s"] for r in results if r["latency_s"] > 0])

    print(f"\n{BOLD}{YELLOW}== {label} — summary =={RESET}")
    if pending:
        print(f"  {YELLOW}({pending} of {len(results)} entries not yet scored for correctness — "
              f"accuracy below is out of scored entries only){RESET}")
    print(f"  {'Case':<10} {'Accuracy':<16} {'Avg latency':<12}")
    print(f"  {'-'*10} {'-'*16} {'-'*12}")
    print(f"  {'Box':<10} {box_correct}/{len(box_scored)} ({pct(box_correct, len(box_scored))})".ljust(28) + f" {box_lat:.1f}s")
    print(f"  {'Crop':<10} {crop_correct}/{len(crop_scored)} ({pct(crop_correct, len(crop_scored))})".ljust(28) + f" {crop_lat:.1f}s")
    print(f"  {'Overall':<10} {total_correct}/{len(all_scored)} ({pct(total_correct, len(all_scored))})".ljust(28) + f" {overall_lat:.1f}s")

    return dict(label=label, box_correct=box_correct, box_total=len(box_scored), box_lat=box_lat,
                crop_correct=crop_correct, crop_total=len(crop_scored), crop_lat=crop_lat,
                total_correct=total_correct, total=len(all_scored), overall_lat=overall_lat)

def compare(stats):
    if len(stats) < 2:
        return
    print(f"\n{BOLD}{CYAN}== Head-to-head =={RESET}")
    a, b = stats[0], stats[1]
    def winner(va, vb, higher_is_better=True):
        if va == vb:
            return "tie"
        better = a["label"] if (va > vb) == higher_is_better else b["label"]
        return f"{GREEN}{better}{RESET}"
    print(f"  Overall accuracy : {a['label']}={pct(a['total_correct'],a['total'])}  vs  "
          f"{b['label']}={pct(b['total_correct'],b['total'])}   -> {winner(a['total_correct']/max(a['total'],1), b['total_correct']/max(b['total'],1))}")
    print(f"  Box accuracy     : {a['label']}={pct(a['box_correct'],a['box_total'])}  vs  "
          f"{b['label']}={pct(b['box_correct'],b['box_total'])}   -> {winner(a['box_correct']/max(a['box_total'],1), b['box_correct']/max(b['box_total'],1))}")
    print(f"  Crop accuracy    : {a['label']}={pct(a['crop_correct'],a['crop_total'])}  vs  "
          f"{b['label']}={pct(b['crop_correct'],b['crop_total'])}   -> {winner(a['crop_correct']/max(a['crop_total'],1), b['crop_correct']/max(b['crop_total'],1))}")
    print(f"  Overall latency  : {a['label']}={a['overall_lat']:.1f}s  vs  "
          f"{b['label']}={b['overall_lat']:.1f}s   -> {winner(a['overall_lat'], b['overall_lat'], higher_is_better=False)} (faster)")

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    stats = []
    for arg in sys.argv[1:]:
        label, path = arg.split(":", 1)
        results = load(path)
        print_table(label, results)
        stats.append(summarize(label, results))
    compare(stats)
    print()

if __name__ == "__main__":
    main()
