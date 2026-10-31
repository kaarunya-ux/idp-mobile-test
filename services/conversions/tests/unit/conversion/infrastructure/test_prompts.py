"""Prompts are versioned files with a checked header (LLM-02)."""

from pathlib import Path

import pytest

from idox_conversion.conversion.infrastructure.model.prompts import load_prompt

_PROMPTS = Path(__file__).resolve().parents[4] / "prompts"


def test_the_shipped_page_prompt_loads_with_both_sections() -> None:
    prompt = load_prompt(_PROMPTS, "read_page", 1)

    assert prompt.name == "read_page"
    assert prompt.version == 1
    assert "exactly as printed" in prompt.system
    assert prompt.user.startswith("Count the paragraphs")


def test_the_shipped_short_page_prompt_loads() -> None:
    assert load_prompt(_PROMPTS, "read_short_page", 1).user


def test_file_whose_header_names_another_prompt_is_refused(tmp_path: Path) -> None:
    (tmp_path / "read_page.v1.md").write_text("---\nname: other\nversion: 1\n---\n## system\na\n## user\nb\n", "utf-8")

    with pytest.raises(ValueError, match="front matter"):
        load_prompt(tmp_path, "read_page", 1)


def test_file_without_a_user_section_is_refused(tmp_path: Path) -> None:
    (tmp_path / "read_page.v1.md").write_text("---\nname: read_page\nversion: 1\n---\n## system\na\n", "utf-8")

    with pytest.raises(ValueError, match="'## user'"):
        load_prompt(tmp_path, "read_page", 1)
