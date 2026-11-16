"""Which prompt versions a golden run uses, and where its results go, so two prompt versions can be run side by side.

``GOLDEN_PROMPT_PINS``   JSON like ``{"page-read": 2}``: pins those prompts at that version, as ``PROMPTS__PINS`` does in the
                         gateway. Unset: the highest version of every prompt, as in a default gateway.
``GOLDEN_RESULTS_TAG``   a name for this variant. Results go to ``results/<tag>/`` instead of ``results/``.
"""

import json
import os
from pathlib import Path


def pins() -> dict[str, int]:
    raw = os.environ.get("GOLDEN_PROMPT_PINS", "").strip()
    return {str(name): int(version) for name, version in json.loads(raw).items()} if raw else {}


def tag() -> str:
    return os.environ.get("GOLDEN_RESULTS_TAG", "").strip()


def results_dir(suite: Path) -> Path:
    path = suite / "results" / tag() if tag() else suite / "results"
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_prompts():  # noqa: ANN201 - the gateway's PromptFiles; imported here because only the gateway's environment has it
    from ai_gateway.ai.infrastructure.prompt_files import PromptFiles  # noqa: PLC0415

    return PromptFiles.load(pins=pins() or None)
