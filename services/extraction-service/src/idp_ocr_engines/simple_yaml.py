"""The few YAML forms this library reads, without a YAML library.

The Python Engineering Standard (ch. 22.3) lists no YAML parser, but ML-01 names the registry ``models.yaml`` and
PaddleOCR ships ``inference.yml``. Both use a narrow subset, so this module reads exactly that and nothing more:
whole-line ``#`` comments, ``key: value`` lines, ``- item`` lists, and plain, single-quoted or double-quoted values.
The readers built on it refuse any other shape instead of guessing.
"""

import json

from idp_ocr_engines.errors import ModelFileError

_MIN_QUOTED = 2


def decode_scalar(text: str) -> str:
    """The text of one YAML scalar: ``'it''s'`` is ``it's``, ``"\\u3042"`` is あ, and a plain value is kept as is."""
    if len(text) >= _MIN_QUOTED and text.startswith("'") and text.endswith("'"):
        return text[1:-1].replace("''", "'")
    if len(text) >= _MIN_QUOTED and text.startswith('"') and text.endswith('"'):
        try:
            decoded: str = json.loads(text)
        except json.JSONDecodeError as error:
            message = "A YAML file has a quoted value this reader does not understand."
            raise ModelFileError(message) from error
        return decoded
    return text.strip(" ")  # only ASCII spaces: a plain item may be U+3000 (ideographic space)
