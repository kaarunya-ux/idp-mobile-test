"""Putting document content into a prompt as data, never as instructions (LLM-04, IDP-03 ch. 4.4).

Every piece of document text (OCR words, readings, a current result, a user's question) goes inside a named block.
The system prompts say content inside these blocks is data. Text that tries to close a block early is defused, so a
document that says "ignore the instructions" stays inside its block.
"""

import json

_OPEN = "<<<DATA name={name}>>>"
_CLOSE = "<<<END DATA>>>"
_MARK = "<<<"
_DEFUSED = "< < <"


def data_block(name: str, content: object) -> str:
    """``content`` (text, or anything JSON can hold) inside a delimited data block."""
    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, separators=(",", ":"))
    return f"{_OPEN.format(name=name)}\n{text.replace(_MARK, _DEFUSED)}\n{_CLOSE}"
