"""What a golden run was made with (prompt drift strategies 1 to 3): the environment name, the build, the prompts, the
behaviour settings and the model server. Two runs with the same stamp should give the same results; a difference in the
stamp explains a difference in the results before anyone compares them.

No document text and no personal data: names, versions and hashes only (OBS-04, LLM-06).
"""

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

GOLDEN = Path(__file__).resolve().parents[1]
REPO = GOLDEN.parents[1]  # the repository root, for git
SERVICES = GOLDEN.parent
sys.path.insert(0, str(GOLDEN))


def environment_name() -> str:
    """``GOLDEN_ENV``: which environment this run measures (dev, preprod, prod ...). Baselines are kept per name."""
    return os.environ.get("GOLDEN_ENV", "dev")


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, check=True, timeout=20).stdout.strip()  # noqa: S603, S607
    except (OSError, subprocess.SubprocessError):
        return ""


def _server() -> dict[str, object]:
    """The model server's own description: the model it serves and the build id it reports."""
    from model import BASE_URL, CHAT_URL  # noqa: PLC0415 - needs GOLDEN_MODEL_URL: only when a server is asked

    info: dict[str, object] = {"model": None, "server_fingerprint": None}
    try:
        models = httpx.get(f"{BASE_URL}/models", timeout=10).json()
        info["model"] = (models.get("data") or [{}])[0].get("id")
        reply = httpx.post(
            CHAT_URL, json={"messages": [{"role": "user", "content": "Reply with one word."}], "max_tokens": 1, "temperature": 0}, timeout=60
        ).json()
        info["server_fingerprint"] = reply.get("system_fingerprint")
    except (httpx.HTTPError, ValueError, KeyError):
        info["server_error"] = "unreachable"
    return info


def _gateway() -> dict[str, object]:
    """The gateway's own prompt hash and behaviour hash, when this runs in the gateway's environment."""
    sys.path.insert(0, str(SERVICES / "ai-gateway" / "src"))
    try:
        from ai_gateway.ai.infrastructure.model_registry import ModelRegistry  # noqa: PLC0415
        from ai_gateway.ai.infrastructure.prompt_files import PromptFiles  # noqa: PLC0415
        from ai_gateway.core.build_info import settings_fingerprint  # noqa: PLC0415
        from ai_gateway.core.config import Settings  # noqa: PLC0415
    except ImportError:
        return {}
    sys.path.insert(0, str(GOLDEN))
    import variant  # noqa: PLC0415

    prompts = PromptFiles.load(pins=variant.pins() or None)
    registry = ModelRegistry.from_file(SERVICES / "ai-gateway" / "models.yaml")
    extras = {"prompts": prompts.fingerprint(), "models": registry.fingerprint()}  # as the gateway's main builds them
    return {
        "prompts_hash": prompts.fingerprint(),
        "prompt_pins": variant.pins(),
        "models_hash": registry.fingerprint(),
        "config_hash": settings_fingerprint(Settings(_env_file=None), extras=extras),
        "prompt_versions": {name: prompts.get(name).version for name in ("page-read", "card-read", "field-read", "classify")},
    }


def collect() -> dict[str, object]:
    dirty = bool(_git("status", "--porcelain"))
    return {
        "env": environment_name(),
        "commit": (_git("rev-parse", "--short", "HEAD") or "unknown") + ("+dirty" if dirty else ""),
        "captured_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        **_gateway(),
        **_server(),
        "temperature": 0,
        "thinking": False,  # the golden calls always send it off (ML-06)
    }


def write(results: Path, passed: bool, problems: list[str]) -> None:
    """``stamp.json`` and ``status.json`` next to a suite's results."""
    results.mkdir(exist_ok=True)
    (results / "stamp.json").write_text(json.dumps(collect(), indent=1), encoding="utf-8")
    (results / "status.json").write_text(json.dumps({"pass": passed, "problems": problems}, indent=1), encoding="utf-8")
