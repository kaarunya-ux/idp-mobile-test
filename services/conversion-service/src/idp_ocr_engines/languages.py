"""Checking a requested language list against what an engine can read (ML-10)."""

from collections.abc import Collection, Sequence

from idp_ocr_engines.errors import UnsupportedLanguageError


def require_languages(requested: Sequence[str], supported: Collection[str], *, engine_name: str) -> frozenset[str]:
    """The requested codes as a set; raises ``UnsupportedLanguageError`` if any is not in ``supported``."""
    wanted = frozenset(code.lower() for code in requested)
    if not wanted:
        message = f"{engine_name} needs at least one language."
        raise UnsupportedLanguageError(message)
    missing = sorted(wanted - set(supported))
    if missing:
        message = f"{engine_name} cannot read: {', '.join(missing)}."
        raise UnsupportedLanguageError(message)
    return wanted
