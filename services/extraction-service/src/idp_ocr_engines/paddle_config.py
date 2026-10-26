"""Reads the ``PostProcess`` section of a PaddleOCR ``inference.yml`` without a YAML library (see ``simple_yaml``).

This reads only the one shape those files use:
``key: value`` lines and ``- item`` lists, two spaces in, under a top-level ``PostProcess:``. Anything else in the file
is ignored. Values are plain, single-quoted or double-quoted. A file that does not look like this raises
``ModelFileError`` instead of being guessed at.
"""

from pathlib import Path

from idp_ocr_engines.errors import ModelFileError
from idp_ocr_engines.simple_yaml import decode_scalar

_SECTION = "PostProcess:"
_KEY_INDENT = "  "
_ITEM_PREFIX = "  - "


def read_post_process(config_path: Path) -> dict[str, str | list[str]]:
    """The scalars (as text) and lists of ``PostProcess``; raises ``ModelFileError`` if the section is missing."""
    try:
        lines = config_path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        message = "A model's inference.yml could not be read."
        raise ModelFileError(message) from error
    if _SECTION not in lines:
        message = "A model's inference.yml has no PostProcess section."
        raise ModelFileError(message)
    values: dict[str, str | list[str]] = {}
    current: list[str] | None = None
    for line in lines[lines.index(_SECTION) + 1 :]:
        if line and not line.startswith(" "):
            break  # the next top-level section
        if line.startswith(_ITEM_PREFIX) and current is not None:
            current.append(decode_scalar(line[len(_ITEM_PREFIX) :]))
        elif line.startswith(_KEY_INDENT) and not line.startswith(_KEY_INDENT + " ") and ":" in line:
            key, _, rest = line.strip().partition(":")
            current = [] if not rest.strip() else None
            values[key] = current if current is not None else decode_scalar(rest.strip())
    return values
