"""``PromptsPort`` on the prompt files shipped in the package (``ai_gateway/prompts/<task>.v<n>.md``, LLM-02).

Each file starts with front matter (``key: value`` lines between ``---`` lines: name, version, model, temperature,
max_tokens, optionally thinking); the rest is the system prompt. The highest version of each name is used, unless
``PROMPTS__PINS`` fixes an older one. Every file is read and checked once at start-up, so a broken prompt stops the
process instead of failing a call.
"""

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import replace
from importlib import resources

from ai_gateway.ai.domain.models import ModelRole, Prompt

PACKAGE = "ai_gateway.prompts"
_FILE = re.compile(r"^(?P<name>[a-z][a-z-]*)\.v(?P<version>[1-9][0-9]*)\.md$")
_FRONT_MATTER = re.compile(r"\A---\n(?P<head>.*?)\n---\n(?P<body>.*)\Z", re.S)


class PromptFileError(RuntimeError):
    """A prompt file is malformed."""


class PromptFiles:
    def __init__(
        self,
        prompts: Mapping[str, Prompt],
        pinned: frozenset[str] = frozenset(),
    ) -> None:
        self._prompts = dict(prompts)
        self._pinned = pinned

    @classmethod
    def load(cls, package: str = PACKAGE, pins: Mapping[str, int] | None = None) -> "PromptFiles":
        """The highest version of every prompt, except those ``pins`` fix at another version. A pin is how a prompt is
        rolled back: ``{"page-read": 2}`` keeps using ``page-read.v2`` although ``v3`` exists."""
        by_name: dict[str, dict[int, Prompt]] = {}
        for entry in resources.files(package).iterdir():
            match = _FILE.match(entry.name)
            if match is None:
                continue
            prompt = parse_prompt(entry.read_text(encoding="utf-8"), file_name=entry.name)
            if (prompt.name, prompt.version) != (match["name"], int(match["version"])):
                raise PromptFileError(f"{entry.name}: its front matter names a different prompt or version")
            by_name.setdefault(prompt.name, {})[prompt.version] = prompt
        chosen: dict[str, Prompt] = {}
        for name, versions in by_name.items():
            version = (pins or {}).get(name, max(versions))
            if version not in versions:
                raise PromptFileError(
                    f"{name} is pinned to version {version}, which does not exist (have {sorted(versions)})"
                )
            chosen[name] = versions[version]
        unknown = sorted(set(pins or {}) - set(by_name))
        if unknown:
            raise PromptFileError(f"PROMPTS__PINS names prompts that do not exist: {', '.join(unknown)}")
        return cls(chosen, frozenset(pins or {}))

    def loaded(self) -> list[tuple[Prompt, bool]]:
        """Every prompt in use and whether a pin chose its version, in name order (for the start-up log)."""
        return [(prompt, name in self._pinned) for name, prompt in sorted(self._prompts.items())]

    def fingerprint(self) -> str:
        """A short hash of every prompt as the model sees it (text and the call settings in its front matter). It
        changes when a prompt file is added, edited or gets a new version (prompt drift strategy 3)."""
        parts = [[p.name, p.version, p.fingerprint] for _, p in sorted(self._prompts.items())]
        return hashlib.sha256(json.dumps(parts).encode()).hexdigest()[:12]

    def get(self, name: str) -> Prompt:
        try:
            return self._prompts[name]
        except KeyError:
            raise PromptFileError(f"no prompt named {name}") from None


def parse_prompt(text: str, *, file_name: str) -> Prompt:
    match = _FRONT_MATTER.match(text)
    if match is None:
        raise PromptFileError(f"{file_name}: no front matter")
    head: dict[str, str] = {}
    for line in match["head"].splitlines():
        key, sep, value = line.partition(":")
        if not sep:
            raise PromptFileError(f"{file_name}: front matter line without a colon")
        head[key.strip()] = value.strip()
    try:
        prompt = Prompt(
            name=head["name"],
            version=int(head["version"]),
            system=match["body"].strip(),
            temperature=float(head["temperature"]),
            max_tokens=int(head["max_tokens"]),
            model=ModelRole(head["model"]) if "model" in head else None,
            thinking=_flag(head["thinking"]) if "thinking" in head else None,
        )
        return replace(prompt, fingerprint=prompt_fingerprint(prompt))
    except (KeyError, ValueError) as exc:
        raise PromptFileError(f"{file_name}: front matter is missing or has a wrong value") from exc


def prompt_fingerprint(prompt: Prompt) -> str:
    """A short hash of what the model sees of a prompt: its text and the call settings in its front matter."""
    parts = [
        prompt.name,
        prompt.version,
        prompt.system,
        prompt.temperature,
        prompt.max_tokens,
        prompt.model.value if prompt.model else None,
        prompt.thinking,
    ]
    return hashlib.sha256(json.dumps(parts).encode()).hexdigest()[:12]


def _flag(value: str) -> bool:
    if value not in ("true", "false"):
        raise ValueError(f"not true or false: {value}")
    return value == "true"
