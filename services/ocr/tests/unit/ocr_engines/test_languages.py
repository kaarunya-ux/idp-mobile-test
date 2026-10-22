"""A language an engine cannot read is an error, never a quiet substitution (ML-10)."""

import pytest

from idp_ocr_engines.errors import UnsupportedLanguageError
from idp_ocr_engines.languages import require_languages


def test_returns_the_codes_as_a_set_when_all_are_supported() -> None:
    assert require_languages(["en", "HI"], {"en", "hi"}, engine_name="demo") == {"en", "hi"}


@pytest.mark.parametrize("requested", [["ta"], ["en", "ta"], ["xx", "yy"]])
def test_refuses_the_request_when_a_language_is_not_supported(requested: list[str]) -> None:
    with pytest.raises(UnsupportedLanguageError, match="cannot read"):
        require_languages(requested, {"en", "hi"}, engine_name="demo")


def test_refuses_the_request_when_no_language_is_given() -> None:
    with pytest.raises(UnsupportedLanguageError, match="at least one"):
        require_languages([], {"en"}, engine_name="demo")
