"""The promotion gate (prompt drift strategies 19 and 21): run it in the environment a build was just promoted to, after
the golden suites ran there.

    GOLDEN_ENV=preprod python gate.py --from dev [--accept-change "reason"]

It passes only when
1. the golden suites have no failure that neither this environment's baseline nor the source environment's baseline accepted;
2. the prompt and configuration hashes equal the source environment's recorded ones (only what may differ by environment
   differs); a deliberate change needs ``--accept-change`` with its reason, and an entry in CHANGES.md;
3. quality and performance are within this environment's thresholds of its own baseline (skipped, and said so, for the
   first run of an environment).
On failure it names the last good state to roll back to: commit, prompt hash and configuration hash.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import baseline as baselines  # noqa: E402
import compare  # noqa: E402
import stamp  # noqa: E402
import summary  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from", dest="source", required=True, help="the environment the build comes from")
    parser.add_argument("--accept-change", metavar="REASON", help="a deliberate change of prompt or configuration")
    args = parser.parse_args()
    env = stamp.environment_name()
    source, target = baselines.load(args.source), baselines.load(env)
    if source is None:
        print(f"No baseline for the source environment {args.source}: the gate has nothing to compare the build with.")
        return 1
    stamps, statuses = summary.stamps(), summary.statuses()
    failures: list[str] = []
    accepted = (target or {}).get("accepted_problems", []) + source["accepted_problems"]
    failures += [f"new suite failure: {p}" for p in compare.new_problems(statuses, accepted)]
    for suite, current in stamps.items():
        for field in ("prompts_hash", "config_hash"):
            if current.get(field) != source["stamp"][suite].get(field):
                message = f"{suite} {field} differs from {args.source}: {source['stamp'][suite].get(field)} -> {current.get(field)}"
                if args.accept_change:
                    print(f"accepted change ({args.accept_change}): {message}")
                else:
                    failures.append(message + " (use --accept-change with a reason, and record it in CHANGES.md)")
    if target is None:
        print(f"No baseline for {env} yet: quality and performance are not compared; record one with baseline.py after this run.")
    else:
        _, drift_failures = compare.compare_performance(summary.summarise(), target["classes"], compare.thresholds_for(env))
        failures += drift_failures
    if failures:
        print(f"GATE FAILED for {env}:\n  " + "\n  ".join(failures))
        if target:
            last = target["stamp"]["conversion"]
            print(f"Last good {env}: commit {last.get('commit')}, prompts {last.get('prompts_hash')}, config {last.get('config_hash')}")
        return 1
    print(f"GATE PASSED for {env} (from {args.source})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
