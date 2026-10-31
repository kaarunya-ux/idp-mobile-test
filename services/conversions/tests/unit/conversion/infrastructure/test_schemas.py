"""The model's answer is validated, then turned into the document model (LLM-03)."""

import json

import pytest
from pydantic import ValidationError

from idox_conversion.conversion.domain.document_model import TableBlock, TextBlock, TextKind
from idox_conversion.conversion.infrastructure.model.schemas import PageAnswer, to_page


def _answer(blocks: list[dict[str, object]]) -> PageAnswer:
    return PageAnswer.model_validate_json(json.dumps({"analysis": {"n_blocks": len(blocks)}, "blocks": blocks}))


def test_text_and_table_blocks_become_document_model_blocks() -> None:
    page = to_page(
        _answer(
            [
                {"kind": "heading", "text": "Title"},
                {
                    "kind": "table",
                    "has_header": True,
                    "n_data_rows": 1,
                    "n_cols": 2,
                    "header": ["A", "B"],
                    "rows": [["1", "2"]],
                },
            ]
        )
    )

    assert page.blocks[0] == TextBlock(kind=TextKind.HEADING, text="Title")
    assert page.blocks[1] == TableBlock(header=("A", "B"), rows=(("1", "2"),), columns=2)


def test_short_rows_are_padded_and_long_rows_are_cut_to_the_table_width() -> None:
    page = to_page(
        _answer(
            [
                {
                    "kind": "table",
                    "has_header": False,
                    "n_data_rows": 2,
                    "n_cols": 3,
                    "header": [],
                    "rows": [["1"], ["1", "2", "3", "4"]],
                }
            ]
        )
    )

    assert page.tables[0].rows == (("1", "", ""), ("1", "2", "3"))


def test_unknown_block_kind_is_not_an_answer() -> None:
    with pytest.raises(ValidationError):
        _answer([{"kind": "chart", "text": "x"}])


def test_answer_without_the_analysis_is_not_an_answer() -> None:
    with pytest.raises(ValidationError):
        PageAnswer.model_validate_json('{"blocks": []}')
