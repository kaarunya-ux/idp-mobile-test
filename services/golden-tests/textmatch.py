"""Word matching shared by the golden tests."""

import re


def words_of(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def norm(text: object) -> str:
    return " ".join(words_of(str(text or "")))


def squash(text: str) -> str:
    return re.sub(r"[^a-z0-9@.]", "", text.lower())


def leaks(text: str, tokens: list[str]) -> list[str]:
    """The tokens found in the text, as written or with spaces and punctuation removed."""
    lowered, squashed = text.lower(), squash(text)
    return [token for token in tokens if token.lower() in lowered or squash(token) in squashed]
