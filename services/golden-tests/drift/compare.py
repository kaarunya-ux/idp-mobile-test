"""Compares the latest golden results with this environment's baseline (prompt drift strategies 9 to 15).

    GOLDEN_ENV=preprod python compare.py

- quality (word recall, cell match, headings, card accuracy, hostile cards obeyed) per class, against the baseline: a drop
  larger than ``quality_drop_max`` fails (the suites' own pass marks are the same in every environment);
- performance as ratios to this environment's own baseline: seconds per page, seconds per megapixel, tokens per page. The
  thresholds are per environment in ``thresholds.json``;
- what changed in the stamp (prompts, configuration hash, model, server build, commit), which explains a difference before
  anyone starts looking for it.
Exit code 1 when a limit is exceeded or a suite has a failure the baseline did not accept.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import baseline as baselines  # noqa: E402
import stamp  # noqa: E402
import summary  # noqa: E402

QUALITY = ("word_recall", "cell_match", "heading_recall", "normal_accuracy")
LATENCY = ("seconds_per_page_median", "seconds_per_megapixel_median", "seconds_per_card_median")
TOKENS = ("tokens_per_page_median", "tokens_per_card_median")
STAMP_FIELDS = ("commit", "prompts_hash", "models_hash", "config_hash", "model", "server_fingerprint", "prompt_versions")


def thresholds_for(env: str) -> dict[str, float]:
    return json.loads((Path(__file__).resolve().parent / "thresholds.json").read_text())[env]


def stamp_changes(current: dict, recorded: dict) -> list[str]:
    changes = []
    for suite in ("conversion", "extraction"):
        for field in STAMP_FIELDS:
            before, now = recorded[suite].get(field), current[suite].get(field)
            if before != now and not (field == "commit" and str(now).split("+")[0] == str(before).split("+")[0]):
                changes.append(f"{suite} {field}: {before} -> {now}")
    return changes


def new_problems(statuses: dict, accepted: list[str]) -> list[str]:
    return [p for suite in statuses.values() for p in suite["problems"] if p not in accepted]


def compare_performance(current: dict, recorded: dict, limits: dict[str, float]) -> tuple[list[str], list[str]]:
    """(table rows, failures) for quality and performance, class by class."""
    rows, failures = [], []
    for name, now in current.items():
        before = recorded.get(name)
        if before is None:
            rows.append(f"| {name} | (no baseline for this class) | | |")
            continue
        for metric in QUALITY:
            if now.get(metric) is not None and before.get(metric) is not None:
                drop = before[metric] - now[metric]
                rows.append(f"| {name} | {metric} | {before[metric]} -> {now[metric]} | drop {drop:+.3f} |")
                if drop > limits["quality_drop_max"]:
                    failures.append(f"{name}: {metric} fell {drop:.3f} (limit {limits['quality_drop_max']})")
        if now.get("hostile_obeyed", 0) > before.get("hostile_obeyed", 0):
            failures.append(f"{name}: hostile cards obeyed {before['hostile_obeyed']} -> {now['hostile_obeyed']}")
        for metrics, limit_key in ((LATENCY, "latency_ratio_max"), (TOKENS, "tokens_ratio_max")):
            for metric in metrics:
                if now.get(metric) and before.get(metric):
                    ratio = now[metric] / before[metric]
                    rows.append(f"| {name} | {metric} | {before[metric]} -> {now[metric]} | x{ratio:.2f} |")
                    if ratio > limits[limit_key]:
                        failures.append(f"{name}: {metric} x{ratio:.2f} (limit x{limits[limit_key]})")
    return rows, failures


def main() -> int:
    env = stamp.environment_name()
    recorded = baselines.load(env)
    if recorded is None:
        print(f"No baseline for {env}: run the suites with GOLDEN_ENV={env}, then python baseline.py")
        return 1
    limits = thresholds_for(env)
    current_stamps, statuses = summary.stamps(), summary.statuses()
    rows, failures = compare_performance(summary.summarise(), recorded["classes"], limits)
    failures += [f"new suite failure: {p}" for p in new_problems(statuses, recorded["accepted_problems"])]
    print(f"# Drift against the {env} baseline recorded {recorded['recorded_at']}\n")
    changes = stamp_changes(current_stamps, recorded["stamp"])
    print("What changed since the baseline:" + ("\n  " + "\n  ".join(changes) if changes else " nothing (same stamp)"))
    print("\n| Class | Metric | Baseline -> now | Change |\n|---|---|---|---|\n" + "\n".join(rows))
    print("\nRESULT: " + ("PASS" if not failures else "FAIL\n  " + "\n  ".join(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
