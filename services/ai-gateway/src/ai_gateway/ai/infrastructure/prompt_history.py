"""What changed in the prompt files since the gateway last started (prompt drift strategy 3).

At every start-up the gateway compares the prompt files with the copy it kept at the previous start-up and logs, for
each file that was added, edited or removed: the file name, its full path and folder, whether the front matter (the
call settings), the text or the whole prompt changed, and the changed lines with their line numbers (and the rule
number when the line is a numbered rule). Prompts are instructions written by the team, not document content, so
their lines may be logged. Only what changed between two start-ups is seen: a file edited twice shows the net change.
"""

import difflib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

WHOLE_PROMPT_SHARE = 0.7  # more than this share of the lines changed: say "whole prompt", not "part of the prompt"
MAX_CHANGES = 20  # changed lines listed per file
FRONT_MATTER_FENCES = 2  # the two --- lines around the front matter
MAX_LINE = 240  # characters of a changed line that are logged
_RULE = re.compile(r"^(\d+)\.\s")


@dataclass(frozen=True)
class LineChange:
    line: int  # the line number in the new file (in the old file for a removed line)
    was: str | None
    now: str | None
    rule: int | None = None

    def as_log(self) -> dict[str, object]:
        out: dict[str, object] = {"line": self.line, "was": self.was, "now": self.now}
        if self.rule is not None:
            out["rule"] = self.rule
        return out


@dataclass(frozen=True)
class PromptFileChange:
    kind: str  # added | edited | removed
    file: str
    path: str
    folder: str
    part: str  # new file | file removed | front matter | text | front matter and text | whole prompt
    lines_added: int
    lines_removed: int
    changed_share: float
    changes: tuple[LineChange, ...]
    truncated: bool


def read_history(path: Path) -> dict[str, str] | None:
    """The prompt texts kept at the previous start-up, or None when there is no (readable) copy."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        files = data["files"]
        return {str(name): str(text) for name, text in files.items()}
    except (OSError, ValueError, KeyError, AttributeError):
        return None


def write_history(path: Path, files: Mapping[str, str]) -> None:
    """Keep the current prompt texts for the next start-up. Written to a temporary file first, then moved into place."""
    path.parent.mkdir(parents=True, exist_ok=True)
    scratch = path.with_name(path.name + ".tmp")
    scratch.write_text(json.dumps({"files": dict(files)}, indent=1, sort_keys=True), encoding="utf-8")
    scratch.replace(path)


def diff_prompt_files(
    old: Mapping[str, str], new: Mapping[str, str], paths: Mapping[str, str]
) -> list[PromptFileChange]:
    """Pure: the previous texts and the current ones in, one entry per file that differs out."""
    found: list[PromptFileChange] = []
    for name in sorted(set(old) | set(new)):
        location = paths.get(name, name)
        folder = str(Path(location).parent)
        if name not in old:
            found.append(_whole("added", name, location, folder, new[name], "new file"))
        elif name not in new:
            found.append(_whole("removed", name, location, folder, old[name], "file removed"))
        elif old[name] != new[name]:
            found.append(_edit(name, location, folder, old[name], new[name]))
    return found


def _whole(kind: str, name: str, path: str, folder: str, text: str, part: str) -> PromptFileChange:
    lines = len(text.splitlines())
    added, removed = (lines, 0) if kind == "added" else (0, lines)
    return PromptFileChange(kind, name, path, folder, part, added, removed, 1.0, (), False)


def _front_matter_end(lines: list[str]) -> int:
    """Index of the line that closes the front matter (the second ``---``), or -1 when there is none."""
    seen = 0
    for index, line in enumerate(lines):
        if line.strip() == "---":
            seen += 1
            if seen == FRONT_MATTER_FENCES:
                return index
    return -1


def _clip(line: str | None) -> str | None:
    return None if line is None else line if len(line) <= MAX_LINE else line[: MAX_LINE - 1] + "…"


def _rule(line: str | None) -> int | None:
    match = _RULE.match(line or "")
    return int(match.group(1)) if match else None


def _edit(name: str, path: str, folder: str, before: str, after: str) -> PromptFileChange:
    old_lines, new_lines = before.splitlines(), after.splitlines()
    head_end = _front_matter_end(new_lines)
    changes: list[LineChange] = []
    added = removed = 0
    in_head = in_text = False
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        removed += i2 - i1
        added += j2 - j1
        in_head = in_head or j1 <= head_end
        in_text = in_text or j2 - 1 > head_end or (j1 > head_end)
        for offset in range(max(i2 - i1, j2 - j1)):
            was = old_lines[i1 + offset] if i1 + offset < i2 else None
            now = new_lines[j1 + offset] if j1 + offset < j2 else None
            line = j1 + offset + 1 if now is not None else j1 + 1
            changes.append(LineChange(line, _clip(was), _clip(now), _rule(now or was)))
    body = max(len(old_lines) - _front_matter_end(old_lines) - 1, len(new_lines) - head_end - 1, 1)
    share = round(min(1.0, max(added, removed) / body), 2)
    if share > WHOLE_PROMPT_SHARE and in_text:
        part = "whole prompt"
    else:
        part = "front matter and text" if in_head and in_text else "front matter" if in_head else "text"
    kept = tuple(changes[:MAX_CHANGES])
    return PromptFileChange("edited", name, path, folder, part, added, removed, share, kept, len(changes) > MAX_CHANGES)
