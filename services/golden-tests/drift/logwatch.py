"""Drift alerts and distributions from the services' JSON logs alone (prompt drift strategies 15 and 16), with no
metrics server or dashboard: the logs are the monitoring.

    python logwatch.py record --env preprod /var/log/idp/*.jsonl      learn what normal looks like from a good period
    python logwatch.py check  --env preprod --since 1h /var/log/idp/*.jsonl   compare a window with it, print alerts

Input is JSON log lines (one object per line) from files or ``-`` (stdin): ``docker logs``, journald or a log shipper's
files all work. Lines that are not JSON are ignored. ``--since`` counts back from the newest event in the input, so a
replayed or archived log gives the same answer as a live one.

What it reads (all names and numbers, no document text): ``ai_call_finished`` (gateway: task, outcome, latency, tokens,
repaired), ``page_read_shape`` (blocks written against blocks counted), ``page_read`` (conversion), ``page_reading_rejected``
and ``model_registry_mismatch``, plus the build stamp on every line (``service``, ``env``, ``service_version``, ``commit``,
``config_hash``).

``check`` prints a report, then one JSON line per alert (``{"event": "drift_alert", ...}``) and a final
``{"event": "drift_check", ...}`` line, so the output can be kept in a file or fed to the log pipeline. Exit code 1 when there is
an alert. Limits per environment are the ``logs`` block of ``thresholds.json``.
"""

import argparse
import json
import statistics
import sys
from collections import Counter
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASELINES = HERE / "log_baselines"
LOW_SHAPE_RATIO = 0.8  # a page that wrote fewer than this share of the blocks it counted
OUTPUT_INVALID = "AIG-502-OUTPUT-001"


# ------------------------------------------------------------------ reading
def parse_time(value: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def read_events(lines: Iterable[str]) -> list[dict]:
    events = []
    for line in lines:
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and "event" in event:
            events.append(event)
    return events


def read_paths(paths: Sequence[str]) -> list[dict]:
    lines: list[str] = []
    for path in paths:
        if path == "-":
            lines += sys.stdin.read().splitlines()
        else:
            lines += Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    return read_events(lines)


def parse_since(text: str) -> timedelta:
    units = {"m": "minutes", "h": "hours", "d": "days"}
    if not text or text[-1] not in units or not text[:-1].isdigit():
        raise SystemExit("--since is a number and m, h or d, for example 30m, 1h or 2d")
    return timedelta(**{units[text[-1]]: int(text[:-1])})


def window(events: list[dict], since: timedelta | None) -> list[dict]:
    """The events in the last ``since`` before the newest one."""
    if since is None:
        return events
    times = [t for e in events if (t := parse_time(e.get("timestamp"))) is not None]
    if not times:
        return []
    start = max(times) - since
    return [e for e in events if (t := parse_time(e.get("timestamp"))) is not None and t >= start]


# ------------------------------------------------------------------ summarising
def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 2) if values else None


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))], 2)


def _share(count: int, total: int) -> float | None:
    return round(count / total, 4) if total else None


def summarise(events: list[dict]) -> dict:
    """Per service: calls per task, page shape, conversion pages, rejections, mismatches and the stamps seen."""
    by_service: dict[str, dict] = {}
    for service in sorted({str(e.get("service", "unknown")) for e in events}):
        mine = [e for e in events if str(e.get("service", "unknown")) == service]
        calls: dict[str, dict] = {}
        for task in sorted({str(e["task"]) for e in mine if e["event"] == "ai_call_finished" and "task" in e}):
            done = [e for e in mine if e["event"] == "ai_call_finished" and e.get("task") == task]
            ok = [e for e in done if e.get("outcome") == "OK"]
            calls[task] = {
                "calls": len(done),
                "latency_ms_median": _median([e["latency_ms"] for e in ok if "latency_ms" in e]),
                "latency_ms_p95": _p95([e["latency_ms"] for e in ok if "latency_ms" in e]),
                "tokens_out_median": _median([e["tokens_out"] for e in ok if "tokens_out" in e]),
                "repaired_share": _share(sum(bool(e.get("repaired")) for e in ok), len(ok)),
                "refusal_share": _share(sum(e.get("outcome") == OUTPUT_INVALID for e in done), len(done)),
            }
        ratios = [e["blocks"] / e["counted"] for e in mine if e["event"] == "page_read_shape" and e.get("counted")]
        pages = [e for e in mine if e["event"] == "page_read"]
        rejected = sum(e["event"] == "page_reading_rejected" for e in mine)
        by_service[service] = {
            "prompt_versions": _prompt_versions(mine),
            "prompt_loaded": _prompt_loaded(mine),
            "prompt_files": _prompt_files(mine),
            "calls": calls,
            "shape": {
                "pages": len(ratios),
                "low_share": _share(sum(r < LOW_SHAPE_RATIO for r in ratios), len(ratios)),
                "ratio_median": _median(ratios),
            },
            "conversion_pages": {
                "pages": len(pages),
                "latency_ms_median": _median([e["latency_ms"] for e in pages if "latency_ms" in e]),
                "tokens_out_median": _median([e["tokens_out"] for e in pages if "tokens_out" in e]),
                "rejected": rejected,
                "rejected_share": _share(rejected, len(pages) + rejected),
            },
            "model_mismatches": sum(e["event"] == "model_registry_mismatch" for e in mine),
            "stamps": {field: dict(Counter(str(e[field]) for e in mine if field in e)) for field in ("config_hash", "commit", "service_version", "env")},
        }
    return by_service


def _prompt_name(label: str) -> str:
    return label.rsplit(".v", 1)[0]


def _prompt_versions(events: list[dict]) -> dict[str, dict[str, dict]]:
    """Per prompt name and version (``page-read`` / ``page-read.v3``): when it was first and last seen answering, how many
    calls, which content hashes, and how those calls went."""
    found: dict[str, dict[str, dict]] = {}
    calls = [e for e in events if e["event"] == "ai_call_finished" and e.get("prompt_version")]
    for label in sorted({str(e["prompt_version"]) for e in calls}):
        mine = [e for e in calls if e["prompt_version"] == label]
        ok = [e for e in mine if e.get("outcome") == "OK"]
        times = sorted(str(e["timestamp"]) for e in mine if "timestamp" in e)
        found.setdefault(_prompt_name(label), {})[label] = {
            "calls": len(mine),
            "first_seen": times[0] if times else None,
            "last_seen": times[-1] if times else None,
            "hashes": dict(Counter(str(e["prompt_hash"]) for e in mine if "prompt_hash" in e)),
            "latency_ms_median": _median([e["latency_ms"] for e in ok if "latency_ms" in e]),
            "tokens_out_median": _median([e["tokens_out"] for e in ok if "tokens_out" in e]),
            "repaired_share": _share(sum(bool(e.get("repaired")) for e in ok), len(ok)),
            "refusal_share": _share(sum(e.get("outcome") == OUTPUT_INVALID for e in mine), len(mine)),
        }
    return found


def _prompt_loaded(events: list[dict]) -> list[dict]:
    """What each start-up reported as loaded (``prompt_loaded``), oldest first."""
    keep = ("timestamp", "prompt_version", "prompt_hash", "pinned", "env", "commit")
    loaded = [{k: e.get(k) for k in keep} for e in events if e["event"] == "prompt_loaded"]
    return sorted(loaded, key=lambda e: str(e["timestamp"]))


def _prompt_files(events: list[dict]) -> list[dict]:
    """The gateway's own account of what changed in the prompt files at a start-up (``prompt_file_edited``, ``_added``
    and ``_removed``): which file, its path, which part and which lines. Oldest first."""
    keep = ("timestamp", "event", "file", "path", "folder", "part", "lines_added", "lines_removed", "changes", "env", "commit")
    found = [{k: e.get(k) for k in keep} for e in events if str(e["event"]).startswith("prompt_file_")]
    return sorted(found, key=lambda e: str(e["timestamp"]))


def _file_alerts(env: str, service: str, files: list[dict]) -> list[dict]:
    alerts: list[dict] = []
    for item in files:
        kind = str(item["event"]).removeprefix("prompt_file_")
        lines = "; ".join(
            f"line {c['line']}" + (f" (rule {c['rule']})" if c.get("rule") else "") + f": {c.get('was')!r} -> {c.get('now')!r}"
            for c in (item.get("changes") or [])[:5]
        )
        message = (
            f"prompt file {item['file']} {kind} at {item['timestamp']} in {item.get('env') or env}: {item['part']}, "
            f"+{item['lines_added']} -{item['lines_removed']} lines. Path {item['path']} (folder {item['folder']})"
            + (f". {lines}" if lines else "")
        )
        alerts.append(_alert("PROMPT_FILE_CHANGED", env, service, "info", message, file=item["file"], path=item["path"], part=item["part"], at=item["timestamp"]))
    return alerts


# ------------------------------------------------------------------ checking
def _alert(kind: str, env: str, service: str, severity: str, message: str, **numbers: object) -> dict:
    return {"event": "drift_alert", "alert": kind, "severity": severity, "env": env, "service": service, "message": message, **numbers}


def _ratio_alert(kind: str, env: str, service: str, what: str, before: float | None, now: float | None, limit: float) -> list[dict]:
    if before and now and now / before > limit:
        return [
            _alert(
                kind,
                env,
                service,
                "warn",
                f"{what} is x{now / before:.2f} of normal (limit x{limit})",
                baseline=before,
                now=now,
                ratio=round(now / before, 3),
                limit=limit,
            )
        ]
    return []


def _calls_alerts(env: str, service: str, now: dict, before: dict, limits: dict) -> list[dict]:
    alerts: list[dict] = []
    for task, current in now["calls"].items():
        if current["calls"] < limits["min_calls"]:
            continue
        normal = before["calls"].get(task, {})
        alerts += _ratio_alert(
            "LATENCY", env, service, f"{task} latency (median)", normal.get("latency_ms_median"), current["latency_ms_median"], limits["latency_ratio_max"]
        )
        alerts += _ratio_alert(
            "TOKENS", env, service, f"{task} output tokens (median)", normal.get("tokens_out_median"), current["tokens_out_median"], limits["tokens_ratio_max"]
        )
        for kind, key, limit_key, label in (
            ("REPAIRED", "repaired_share", "repaired_share_max", "answers that needed a repair"),
            ("REFUSED", "refusal_share", "refusal_share_max", "answers refused as unusable"),
        ):
            value = current[key]
            if value is not None and value > limits[limit_key]:
                message = f"{task}: {value:.0%} of {label} (limit {limits[limit_key]:.0%})"
                alerts.append(_alert(kind, env, service, "warn", message, baseline=normal.get(key), now=value))
    return alerts


def _page_alerts(env: str, service: str, now: dict, limits: dict) -> list[dict]:
    alerts: list[dict] = []
    shape = now["shape"]
    if shape["pages"] >= limits["min_calls"] and shape["low_share"] is not None and shape["low_share"] > limits["shape_low_share_max"]:
        alerts.append(
            _alert(
                "SHAPE",
                env,
                service,
                "warn",
                f"{shape['low_share']:.0%} of pages wrote under {LOW_SHAPE_RATIO:.0%} of the blocks they counted (limit {limits['shape_low_share_max']:.0%})",
                now=shape["low_share"],
            )
        )
    pages = now["conversion_pages"]
    if pages["pages"] + pages["rejected"] >= limits["min_calls"] and (pages["rejected_share"] or 0) > limits["rejected_share_max"]:
        alerts.append(
            _alert(
                "REJECTED",
                env,
                service,
                "warn",
                f"{pages['rejected_share']:.0%} of scanned pages were refused by the OCR check (limit {limits['rejected_share_max']:.0%})",
                now=pages["rejected_share"],
            )
        )
    return alerts


def _version_number(label: str) -> int:
    return int(label.rsplit(".v", 1)[1])


def _prompt_timeline(env: str, service: str, loaded: list[dict]) -> list[dict]:
    """A change at a start-up: the version or hash of a prompt differs from what the start-up before it loaded."""
    alerts: list[dict] = []
    last: dict[str, dict] = {}
    for event in loaded:
        name = _prompt_name(str(event["prompt_version"]))
        before = last.get(name)
        if before is not None and (before["prompt_version"], before["prompt_hash"]) != (event["prompt_version"], event["prompt_hash"]):
            rolled_back = event["pinned"] and _version_number(event["prompt_version"]) < _version_number(before["prompt_version"])
            kind = "PROMPT_ROLLED_BACK" if rolled_back else "PROMPT_CHANGED"
            what = "rolled back" if rolled_back else "changed"
            message = (
                f"prompt {name} {what} from {before['prompt_version']} (hash {before['prompt_hash']}) to {event['prompt_version']} "
                f"(hash {event['prompt_hash']}) at {event['timestamp']} in {event.get('env') or env}, commit {event.get('commit')}"
                + (", pinned" if event["pinned"] else "")
            )
            alerts.append(
                _alert(kind, env, service, "info", message, prompt=name, at=event["timestamp"], old=before["prompt_version"], new=event["prompt_version"])
            )
        last[name] = event
    return alerts


def _prompt_results(env: str, service: str, now: dict, before: dict, limits: dict) -> list[dict]:
    """Did a newer version of a prompt do worse than the one before it? Compared from the calls in the logs: the share
    of answers that needed a repair or were refused, speed and size. Quality of the text itself is for the golden suite
    (``prompt_check.py``): the logs only show what a bad prompt costs in repairs, refusals, time and tokens."""
    alerts: list[dict] = []
    for name, versions in now["prompt_versions"].items():
        for label, stats in versions.items():
            if len(stats["hashes"]) > 1:
                alerts.append(
                    _alert(
                        "PROMPT_EDITED",
                        env,
                        service,
                        "warn",
                        f"{label} answered with {len(stats['hashes'])} different texts ({', '.join(stats['hashes'])}): "
                        "a prompt file was edited in place instead of getting a new version",
                        label=label,
                    )
                )
        merged = {**before.get("prompt_versions", {}).get(name, {}), **versions}
        ordered = sorted(merged, key=lambda label: (str(merged[label]["first_seen"]), _version_number(label)))
        for old_label, new_label in zip(ordered, ordered[1:], strict=False):
            old, new = merged[old_label], merged[new_label]
            if min(old["calls"], new["calls"]) < limits["min_calls"] or _version_number(new_label) <= _version_number(old_label):
                continue
            reasons = []
            for key, what in (("repaired_share", "repaired"), ("refusal_share", "refused")):
                delta = (new[key] or 0) - (old[key] or 0)
                if delta > limits["prompt_worse_share_delta"]:
                    reasons.append(f"{what} {old[key] or 0:.0%} -> {new[key] or 0:.0%}")
            for key, what, limit in (("latency_ms_median", "latency", "latency_ratio_max"), ("tokens_out_median", "output tokens", "tokens_ratio_max")):
                if old[key] and new[key] and new[key] / old[key] > limits[limit]:
                    reasons.append(f"{what} x{new[key] / old[key]:.2f}")
            if reasons:
                pin = json.dumps({name: _version_number(old_label)})
                alerts.append(
                    _alert(
                        "PROMPT_WORSE",
                        env,
                        service,
                        "warn",
                        f"{new_label} (first seen {new['first_seen']}) did worse than {old_label}: {'; '.join(reasons)}. "
                        f"Roll back with PROMPTS__PINS='{pin}' and restart; check the text quality with prompt_check.py ab",
                        prompt=name,
                        old=old_label,
                        new=new_label,
                    )
                )
    return alerts


def check(env: str, now: dict, before: dict, limits: dict) -> list[dict]:
    alerts: list[dict] = []
    for service, current in now.items():
        normal = before.get(service, {"calls": {}, "stamps": {}, "prompt_versions": {}})
        if current["model_mismatches"]:
            alerts.append(
                _alert(
                    "MODEL_MISMATCH",
                    env,
                    service,
                    "critical",
                    f"{current['model_mismatches']} start-ups saw a model server serving a model other than models.yaml",
                    now=current["model_mismatches"],
                )
            )
        hashes = current["stamps"].get("config_hash", {})
        if len(hashes) > 1:
            alerts.append(
                _alert(
                    "MIXED_CONFIG",
                    env,
                    service,
                    "warn",
                    f"{len(hashes)} different configuration hashes in one window: replicas run different configurations or a deploy is half done",
                    hashes=hashes,
                )
            )
        known = set(normal["stamps"].get("config_hash", {}))
        changed = sorted(set(hashes) - known) if known else []
        if changed:
            alerts.append(
                _alert(
                    "CONFIG_CHANGED",
                    env,
                    service,
                    "info",
                    f"configuration hash {', '.join(changed)} is new since the baseline ({', '.join(sorted(known))}); "
                    f"commits {', '.join(current['stamps'].get('commit', {}))}",
                    hashes=hashes,
                )
            )
        alerts += _calls_alerts(env, service, current, normal, limits)
        alerts += _page_alerts(env, service, current, limits)
        alerts += _prompt_timeline(env, service, current["prompt_loaded"])
        alerts += _file_alerts(env, service, current.get("prompt_files", []))
        alerts += _prompt_results(env, service, current, normal, limits)
    return alerts


# ------------------------------------------------------------------ the command line
def limits_for(env: str) -> dict:
    return json.loads((HERE / "thresholds.json").read_text())[env]["logs"]


def report(env: str, now: dict, alerts: list[dict]) -> str:
    lines = [f"# Drift from logs, environment {env}", ""]
    for service, s in now.items():
        lines.append(f"## {service}   stamps: " + ", ".join(f"{k}={'/'.join(v) or '-'}" for k, v in s["stamps"].items() if k != "env"))
        for task, c in s["calls"].items():
            lines.append(
                f"  {task}: {c['calls']} calls, latency median {c['latency_ms_median']} ms (p95 {c['latency_ms_p95']}), "
                f"tokens out {c['tokens_out_median']}, repaired {c['repaired_share']}, refused {c['refusal_share']}"
            )
        for name, versions in s["prompt_versions"].items():
            lines.append(f"  prompt {name}: " + "; ".join(f"{label} {v['calls']} calls since {v['first_seen']}" for label, v in versions.items()))
        if s["shape"]["pages"]:
            lines.append(
                f"  page shape: {s['shape']['pages']} pages, blocks/counted median {s['shape']['ratio_median']}, "
                f"under {LOW_SHAPE_RATIO:.0%}: {s['shape']['low_share']}"
            )
        if s["conversion_pages"]["pages"] or s["conversion_pages"]["rejected"]:
            p = s["conversion_pages"]
            lines.append(f"  scanned pages: {p['pages']} read, {p['rejected']} refused, latency median {p['latency_ms_median']} ms")
        lines.append("")
    lines.append("ALERTS: " + (str(len(alerts)) if alerts else "none"))
    lines += [f"  [{a['severity']}] {a['alert']} {a['service']}: {a['message']}" for a in alerts]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["record", "check"])
    parser.add_argument("--env", required=True)
    parser.add_argument("--since", help="only the last N m/h/d before the newest event, for example 1h")
    parser.add_argument("--json", action="store_true", help="print only the JSON alert lines")
    parser.add_argument("--baselines", type=Path, default=BASELINES, help="where baselines are kept (default: log_baselines/)")
    parser.add_argument("logs", nargs="+", help="JSON log files, or - for stdin")
    args = parser.parse_args(argv)
    events = window(read_paths(args.logs), parse_since(args.since) if args.since else None)
    if not events:
        sys.stdout.write(json.dumps({"event": "drift_check", "env": args.env, "result": "no events"}) + "\n")
        return 1
    now = summarise(events)
    path = args.baselines / f"{args.env}.json"
    if args.command == "record":
        args.baselines.mkdir(exist_ok=True)
        path.write_text(
            json.dumps({"env": args.env, "recorded_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "services": now}, indent=1), encoding="utf-8"
        )
        sys.stdout.write(f"log baseline for {args.env} written: {path} ({sum(len(s['calls']) for s in now.values())} task(s), {len(events)} events)\n")
        return 0
    if not path.exists():
        sys.stdout.write(f"No log baseline for {args.env}: run  logwatch.py record --env {args.env} <logs of a good period>\n")
        return 1
    alerts = check(args.env, now, json.loads(path.read_text())["services"], limits_for(args.env))
    if not args.json:
        sys.stdout.write(report(args.env, now, alerts) + "\n")
    for alert in alerts:
        sys.stdout.write(json.dumps(alert) + "\n")
    serious = [a for a in alerts if a["severity"] != "info"]
    sys.stdout.write(
        json.dumps({"event": "drift_check", "env": args.env, "events": len(events), "alerts": len(alerts), "result": "ALERT" if serious else "ok"}) + "\n"
    )
    return 1 if serious else 0


if __name__ == "__main__":
    sys.exit(main())
