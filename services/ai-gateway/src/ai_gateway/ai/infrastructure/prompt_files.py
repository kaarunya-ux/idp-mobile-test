"""``PromptsPort`` on the prompt files shipped in the package (``ai_gateway/prompts/<task>.v<n>.md``, LLM-02).

Each file starts with front matter (``key: value`` lines between ``---`` lines: name, version, model, temperature,
max_tokens); the rest is the system prompt. The highest version of each name is used. Every file is read and checked
once at start-up, so a broken prompt stops the process instead of failing a call.
"""

import re
from collections.abc import Mapping
from importlib import resources

from ai_gateway.ai.domain.models import ModelRole, Prompt

PACKAGE = "ai_gateway.prompts"
_FILE = re.compile(r"^(?P<name>[a-z][a-z-]*)\.v(?P<version>[1-9][0-9]*)\.md$")
_FRONT_MATTER = re.compile(r"\A---\n(?P<head>.*?)\n---\n(?P<body>.*)\Z", re.S)


class PromptFileError(RuntimeError):
    """A prompt file is malformed."""


class PromptFiles:
    def __init__(self, prompts: Mapping[str, Prompt]) -> None:
        self._prompts = dict(prompts)

    @classmethod
    def load(cls, package: str = PACKAGE) -> "PromptFiles":
        latest: dict[str, Prompt] = {}
        for entry in resources.files(package).iterdir():
            match = _FILE.match(entry.name)
            if match is None:
                continue
            prompt = parse_prompt(entry.read_text(encoding="utf-8"), file_name=entry.name)
            if (prompt.name, prompt.version) != (match["name"], int(match["version"])):
                raise PromptFileError(f"{entry.name}: its front matter names a different prompt or version")
            if prompt.name not in latest or prompt.version > latest[prompt.name].version:
                latest[prompt.name] = prompt
        return cls(latest)

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
        return Prompt(
            name=head["name"],
            version=int(head["version"]),
            system=match["body"].strip(),
            temperature=float(head["temperature"]),
            max_tokens=int(head["max_tokens"]),
            model=ModelRole(head["model"]) if "model" in head else None,
        )
    except (KeyError, ValueError) as exc:
        raise PromptFileError(f"{file_name}: front matter is missing or has a wrong value") from exc
