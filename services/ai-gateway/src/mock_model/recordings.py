"""Recorded answers: JSON files in ``recorded/``, each a list of ``{schema, match, answer}``.

An entry applies when the request's schema name is ``schema`` and its question text contains ``match`` (any text,
when ``match`` is empty). The first entry that applies wins. Values in recordings are made up: never a real card's.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

PACKAGE = "mock_model.recorded"


@dataclass(frozen=True, slots=True, kw_only=True)
class Recording:
    schema: str
    match: str
    answer: Any


class RecordingError(RuntimeError):
    """A recordings file is malformed."""


def load_recordings(folder: Path | None = None) -> list[Recording]:
    entries = (
        [(f.name, f.read_text(encoding="utf-8")) for f in sorted(folder.glob("*.json"))]
        if folder is not None
        else [
            (f.name, f.read_text(encoding="utf-8"))
            for f in sorted(resources.files(PACKAGE).iterdir(), key=lambda f: f.name)
            if f.name.endswith(".json")
        ]
    )
    found: list[Recording] = []
    for name, text in entries:
        try:
            found += [
                Recording(schema=str(e["schema"]), match=str(e.get("match") or ""), answer=e["answer"])
                for e in json.loads(text)
            ]
        except (ValueError, KeyError, TypeError) as exc:
            raise RecordingError(f"{name}: each entry needs schema and answer") from exc
    return found


def find_recording(recordings: Sequence[Recording], schema: str, text: str) -> Recording | None:
    return next((r for r in recordings if r.schema == schema and r.match in text), None)
