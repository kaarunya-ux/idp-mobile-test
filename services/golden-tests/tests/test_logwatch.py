"""logwatch: drift distributions and alerts from JSON logs alone. Synthetic log lines: names and numbers only."""

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "drift"))
import logwatch  # noqa: E402

START = datetime(2026, 11, 10, 8, 0, tzinfo=UTC)
STAMP = {"service": "ai-gateway", "env": "preprod", "service_version": "0.1.0", "commit": "abc1234", "config_hash": "aaaaaaaaaaaa"}


def _at(minutes: int) -> str:
    return (START + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _call(minute: int, *, latency_ms: int = 20_000, tokens_out: int = 400, repaired: bool = False, outcome: str = "OK", **stamp: str) -> dict:
    event = {"event": "ai_call_finished", "task": "page-read", "outcome": outcome, "timestamp": _at(minute), **STAMP, **stamp}
    if outcome == "OK":
        event |= {"latency_ms": latency_ms, "tokens_out": tokens_out, "repaired": repaired}
    return event


def _calls(count: int, **kwargs: object) -> list[dict]:
    return [_call(minute, **kwargs) for minute in range(count)]  # type: ignore[arg-type]


def _limits(env: str = "preprod") -> dict:
    return logwatch.limits_for(env)


def _alerts(now_events: list[dict], baseline_events: list[dict], env: str = "preprod") -> dict[str, dict]:
    found = logwatch.check(env, logwatch.summarise(now_events), logwatch.summarise(baseline_events), _limits(env))
    return {alert["alert"]: alert for alert in found}


def test_reads_the_json_lines_and_ignores_everything_else() -> None:
    lines = ["plain text from uvicorn", json.dumps(_call(0)), "{broken json", json.dumps({"no_event": 1}), "", json.dumps(_call(1))]

    assert len(logwatch.read_events(lines)) == 2


def test_summarises_calls_by_task_with_medians_and_shares() -> None:
    events = [_call(0, latency_ms=10_000), _call(1, latency_ms=30_000, repaired=True), _call(2, outcome="AIG-502-OUTPUT-001"), _call(3, latency_ms=20_000)]

    task = logwatch.summarise(events)["ai-gateway"]["calls"]["page-read"]

    assert task["calls"] == 4
    assert task["latency_ms_median"] == 20_000
    assert task["repaired_share"] == pytest.approx(1 / 3, abs=1e-3)  # of the three that answered
    assert task["refusal_share"] == 0.25  # of all four


def test_keeps_only_the_last_window_before_the_newest_event() -> None:
    events = [_call(0), _call(30), _call(100), _call(119)]

    assert len(logwatch.window(events, logwatch.parse_since("30m"))) == 2
    assert len(logwatch.window(events, None)) == 4


def test_raises_no_alert_when_the_window_looks_like_the_baseline() -> None:
    assert _alerts(_calls(30), _calls(30)) == {}


def test_does_not_judge_a_window_with_too_few_calls() -> None:
    assert _alerts(_calls(5, latency_ms=90_000), _calls(30)) == {}  # preprod needs 20 calls


def test_alerts_when_latency_or_tokens_rise_past_the_environments_limit() -> None:
    found = _alerts(_calls(30, latency_ms=40_000, tokens_out=900), _calls(30))

    assert found["LATENCY"]["ratio"] == 2.0
    assert found["TOKENS"]["now"] == 900


def test_alerts_when_too_many_answers_needed_a_repair_or_were_refused() -> None:
    now = [_call(m, repaired=m % 5 == 0) for m in range(30)] + [_call(40 + m, outcome="AIG-502-OUTPUT-001") for m in range(3)]

    found = _alerts(now, _calls(30))

    assert {"REPAIRED", "REFUSED"} <= found.keys()
    assert found["REPAIRED"]["severity"] == "warn"


def test_alerts_when_pages_write_far_fewer_blocks_than_they_counted() -> None:
    shape = [{"event": "page_read_shape", "blocks": 2 if m % 2 else 10, "counted": 10, "timestamp": _at(m), **STAMP} for m in range(30)]

    assert "SHAPE" in _alerts(shape, _calls(30))


def test_alerts_when_the_ocr_check_refuses_too_many_scanned_pages() -> None:
    pages = [{"event": "page_read", "latency_ms": 9000, "tokens_out": 300, "timestamp": _at(m), **STAMP, "service": "conversion-service"} for m in range(25)]
    refused = [{"event": "page_reading_rejected", "timestamp": _at(40 + m), **STAMP, "service": "conversion-service"} for m in range(5)]

    assert "REJECTED" in _alerts(pages + refused, pages)


def test_alerts_critically_when_a_start_up_saw_another_model_than_models_yaml() -> None:
    mismatch = {"event": "model_registry_mismatch", "role": "page", "timestamp": _at(1), **STAMP}

    found = _alerts(_calls(30) + [mismatch], _calls(30))

    assert found["MODEL_MISMATCH"]["severity"] == "critical"


def test_alerts_when_replicas_run_different_configurations_and_marks_a_new_hash_as_information() -> None:
    mixed = _calls(15) + _calls(15, config_hash="bbbbbbbbbbbb", commit="def5678")

    found = _alerts(mixed, _calls(30))

    assert found["MIXED_CONFIG"]["severity"] == "warn"
    assert found["CONFIG_CHANGED"]["severity"] == "info"
    assert "bbbbbbbbbbbb" in found["CONFIG_CHANGED"]["message"]


def test_records_a_baseline_and_checks_a_window_against_it_from_the_command_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(logwatch, "BASELINES", tmp_path / "baselines")
    good, slow = tmp_path / "good.jsonl", tmp_path / "slow.jsonl"
    good.write_text("\n".join(json.dumps(e) for e in _calls(30)) + "\nnot json\n")
    slow.write_text("\n".join(json.dumps(e) for e in _calls(30, latency_ms=60_000)))

    assert logwatch.main(["record", "--env", "preprod", str(good)]) == 0
    assert logwatch.main(["check", "--env", "preprod", str(good)]) == 0
    capsys.readouterr()
    assert logwatch.main(["check", "--env", "preprod", "--json", str(slow)]) == 1

    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [line["event"] for line in lines] == ["drift_alert", "drift_check"]
    assert lines[0]["alert"] == "LATENCY"
    assert lines[1]["result"] == "ALERT"


def test_says_so_when_there_is_no_baseline_or_no_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(logwatch, "BASELINES", tmp_path / "none")
    log = tmp_path / "a.jsonl"
    log.write_text(json.dumps(_call(0)))
    empty = tmp_path / "empty.jsonl"
    empty.write_text("only text\n")

    assert logwatch.main(["check", "--env", "preprod", str(log)]) == 1
    assert logwatch.main(["check", "--env", "preprod", str(empty)]) == 1


# --- prompts: when one changed, and whether the new one did worse -------------------------------------------------------


def _loaded(minute: int, label: str, digest: str, *, pinned: bool = False, commit: str = "abc1234") -> dict:
    return {"event": "prompt_loaded", "prompt_version": label, "prompt_hash": digest, "pinned": pinned, "timestamp": _at(minute), **STAMP, "commit": commit}


def _prompt_calls(first: int, count: int, label: str, digest: str, **kwargs: object) -> list[dict]:
    return [_call(first + i, prompt_version=label, prompt_hash=digest, **kwargs) for i in range(count)]  # type: ignore[arg-type]


def test_logs_when_a_prompt_changed_with_the_time_the_environment_and_the_commit() -> None:
    loaded = [_loaded(0, "page-read.v2", "aaaa11112222"), _loaded(120, "page-read.v3", "bbbb33334444", commit="def5678")]

    found = _alerts(loaded + _calls(25), _calls(25))

    message = found["PROMPT_CHANGED"]["message"]
    assert "page-read changed from page-read.v2 (hash aaaa11112222) to page-read.v3 (hash bbbb33334444)" in message
    assert _at(120) in message
    assert "preprod" in message
    assert "def5678" in message
    assert found["PROMPT_CHANGED"]["severity"] == "info"


def test_says_so_when_a_prompt_was_rolled_back_with_a_pin() -> None:
    loaded = [_loaded(0, "page-read.v3", "bbbb33334444"), _loaded(90, "page-read.v2", "aaaa11112222", pinned=True)]

    found = _alerts(loaded + _calls(25), _calls(25))

    assert "PROMPT_CHANGED" not in found
    assert found["PROMPT_ROLLED_BACK"]["old"] == "page-read.v3"
    assert found["PROMPT_ROLLED_BACK"]["new"] == "page-read.v2"
    assert "pinned" in found["PROMPT_ROLLED_BACK"]["message"]


def test_says_nothing_about_a_prompt_that_was_loaded_the_same_at_every_start_up() -> None:
    loaded = [_loaded(0, "page-read.v2", "aaaa11112222"), _loaded(60, "page-read.v2", "aaaa11112222")]

    assert _alerts(loaded + _calls(25), _calls(25)) == {}


def test_alerts_when_the_text_of_a_version_changed_without_a_new_version() -> None:
    calls = _prompt_calls(0, 15, "page-read.v2", "aaaa11112222") + _prompt_calls(15, 15, "page-read.v2", "cccc55556666")

    found = _alerts(calls, _calls(30))

    assert found["PROMPT_EDITED"]["label"] == "page-read.v2"


def test_says_the_new_prompt_did_worse_and_how_to_roll_back_when_it_needs_more_repairs() -> None:
    old = _prompt_calls(0, 30, "page-read.v2", "aaaa11112222")
    new = [_call(100 + i, prompt_version="page-read.v3", prompt_hash="bbbb33334444", repaired=i % 3 == 0) for i in range(30)]

    found = _alerts(old + new, old)

    worse = found["PROMPT_WORSE"]
    assert (worse["old"], worse["new"]) == ("page-read.v2", "page-read.v3")
    assert "repaired 0% -> 34%" in worse["message"] or "repaired 0% -> 33%" in worse["message"]
    assert """PROMPTS__PINS='{"page-read": 2}'""" in worse["message"]


def test_says_the_new_prompt_did_worse_when_it_is_slower_and_writes_more_tokens() -> None:
    old = _prompt_calls(0, 30, "page-read.v2", "aaaa11112222")
    new = _prompt_calls(100, 30, "page-read.v3", "bbbb33334444", latency_ms=40_000, tokens_out=900)

    message = _alerts(old + new, old)["PROMPT_WORSE"]["message"]

    assert "latency x2.00" in message
    assert "output tokens x2.25" in message


def test_does_not_blame_a_new_prompt_that_does_as_well_as_the_old_one_or_has_too_few_calls() -> None:
    old = _prompt_calls(0, 30, "page-read.v2", "aaaa11112222")

    assert "PROMPT_WORSE" not in _alerts(old + _prompt_calls(100, 30, "page-read.v3", "bbbb33334444"), old)
    assert "PROMPT_WORSE" not in _alerts(old + _prompt_calls(100, 5, "page-read.v3", "bbbb33334444", latency_ms=90_000), old)


def test_names_the_prompt_file_its_path_the_part_and_the_changed_line_when_the_gateway_logged_an_edit() -> None:
    edit = {
        "event": "prompt_file_edited",
        "timestamp": _at(60),
        "service": "ai-gateway",
        "env": "preprod",
        "file": "page-read.v3.md",
        "path": "/srv/ai_gateway/prompts/page-read.v3.md",
        "folder": "/srv/ai_gateway/prompts",
        "part": "text",
        "lines_added": 1,
        "lines_removed": 1,
        "changes": [{"line": 10, "rule": 2, "was": "2. Keep the order.", "now": "2. Keep the reading order."}],
    }

    found = _alerts([edit] + _calls(25), _calls(25))

    message = found["PROMPT_FILE_CHANGED"]["message"]
    assert "page-read.v3.md edited" in message
    assert "/srv/ai_gateway/prompts/page-read.v3.md" in message
    assert "folder /srv/ai_gateway/prompts" in message
    assert "text" in message
    assert "line 10 (rule 2)" in message
