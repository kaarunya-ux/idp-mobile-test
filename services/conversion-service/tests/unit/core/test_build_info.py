"""The build stamp on every log line and the fingerprint of what changes behaviour (prompt drift strategies 1 and 3)."""

import json

import pytest
import structlog

from conversion.core.build_info import BEHAVIOUR_SETTINGS, build_stamp, settings_fingerprint
from conversion.core.config import LoggingSettings, Settings
from conversion.core.logging import configure_logging


def _settings(**changes: object) -> Settings:
    return Settings.model_validate(changes)


def test_gives_the_same_fingerprint_when_the_settings_are_the_same() -> None:
    assert settings_fingerprint(_settings()) == settings_fingerprint(_settings())


def test_changes_the_fingerprint_when_a_setting_that_changes_behaviour_changes() -> None:
    assert settings_fingerprint(_settings()) != settings_fingerprint(_settings(reading_check={"min_share": 0.4}))


def test_keeps_the_fingerprint_when_only_what_may_differ_by_environment_changes() -> None:
    """URLs, the environment name and the log format may differ between dev, pre-prod and prod: a promoted build must
    keep its hash."""
    other = _settings(
        environment="prod",
        logging={"format": "console"},
        ai_gateway={"url": "http://other-host:8002", "page_concurrency": 8},
    )

    assert settings_fingerprint(other) == settings_fingerprint(_settings())


def test_changes_the_fingerprint_when_the_prompt_hash_passed_in_changes() -> None:
    assert settings_fingerprint(_settings(), extras={"prompts": "a"}) != settings_fingerprint(
        _settings(), extras={"prompts": "b"}
    )


def test_names_only_settings_that_exist() -> None:
    """A path that is not a setting would hash as "<unset>" for ever and never notice a change."""
    base = _settings().model_dump(mode="json")
    for path in BEHAVIOUR_SETTINGS:
        node: object = base
        for part in path.split("."):
            assert isinstance(node, dict), path
            assert part in node, path
            node = node[part]


def test_puts_the_stamp_on_every_log_line(capsys: pytest.CaptureFixture[str]) -> None:
    stamp = build_stamp(_settings(environment="dev", build={"commit": "abc1234"}))
    configure_logging(LoggingSettings(level="INFO", format="json"), stamp)

    structlog.get_logger("test").info("something_happened", pages=3)

    record = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert (record["env"], record["commit"]) == ("dev", "abc1234")
    assert record["config_hash"] == stamp.config_hash
    assert record["service_version"] == stamp.version
    assert record["pages"] == 3
