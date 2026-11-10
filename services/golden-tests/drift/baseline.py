"""Records this environment's baseline from the latest golden results (prompt drift strategies 11 and 21).

    GOLDEN_ENV=preprod python baseline.py [--accept-failures "reason"]

The baseline holds the numbers per class and the stamp (commit, prompt and configuration hashes, model server build): the
last good state of that environment, and what to roll back to when a later run is worse. Performance is kept per
environment because hardware, quantisation and load differ; compare a run only with its own environment's baseline.

A suite that currently fails refuses to be recorded, unless the failures are named and accepted with a reason; the
accepted ones are then not counted as new by ``compare.py`` and ``gate.py``.
"""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stamp  # noqa: E402
import summary  # noqa: E402

BASELINES = Path(__file__).resolve().parent / "baselines"


def path_for(env: str) -> Path:
    return BASELINES / f"{env}.json"


def load(env: str) -> dict | None:
    path = path_for(env)
    return json.loads(path.read_text()) if path.exists() else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--accept-failures", metavar="REASON", help="record a run whose suites have known failures")
    args = parser.parse_args()
    env = stamp.environment_name()
    statuses = summary.statuses()
    problems = [p for suite in statuses.values() for p in suite["problems"]]  # type: ignore[index]
    if problems and not args.accept_failures:
        print("These suite failures are not accepted:\n  " + "\n  ".join(problems))
        print('Fix them, or record with --accept-failures "reason" to keep them out of future comparisons.')
        return 1
    stamps = summary.stamps()
    wrong_env = [name for name, s in stamps.items() if s["env"] != env]  # type: ignore[index]
    if wrong_env:
        print(f"The latest {', '.join(wrong_env)} results were made for another environment than {env}: rerun the suites with GOLDEN_ENV={env}.")
        return 1
    BASELINES.mkdir(exist_ok=True)
    data = {
        "env": env,
        "recorded_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "stamp": stamps,
        "classes": summary.summarise(),
        "accepted_problems": problems,
        "accepted_reason": args.accept_failures,
    }
    path_for(env).write_text(json.dumps(data, indent=1), encoding="utf-8")
    print(f"baseline for {env} written: {path_for(env)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
