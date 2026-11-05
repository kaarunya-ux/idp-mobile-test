"""page-layout: the model answers a page's structure by OCR line number; a number not on the page is refused."""

import json

import pytest
from pydantic import BaseModel

from ai_gateway.ai.application import answer_models
from ai_gateway.ai.application.answer_models import LayoutTableBlock, LayoutTextBlock
from ai_gateway.ai.application.services.read_layout import ReadLayoutService, line_numbers
from ai_gateway.ai.domain.exceptions import OutputInvalidError
from ai_gateway.ai.domain.models import ImageRef, ModelRole
from ai_gateway.ai.infrastructure.prompt_files import PromptFiles
from support.fakes import FakeImages, FakeModel, caller, runner

PAGE = ImageRef(data=b"jpeg")
LINES = [
    (0, "MADE-UP TITLE", (0.3, 0.05, 0.4, 0.04)),
    (1, "Item", (0.1, 0.2, 0.2, 0.03)),
    (2, "Qty", (0.5, 0.2, 0.1, 0.03)),
]


def _service(model: FakeModel) -> ReadLayoutService:
    return ReadLayoutService(runner=runner(model), images=FakeImages(), prompts=PromptFiles.load(), max_side_px=2048)


async def test_returns_the_blocks_by_line_number_on_the_page_model_with_the_lines_as_data() -> None:
    layout = {"blocks": [{"kind": "heading", "lines": [0]}, {"kind": "table", "rows": [[[1], [2]]]}]}
    model = FakeModel(json.dumps(layout))

    answer = await _service(model).read(caller(), PAGE, LINES)

    heading, table = answer.value.blocks
    assert isinstance(heading, LayoutTextBlock)
    assert heading.lines == [0]
    assert isinstance(table, LayoutTableBlock)
    assert line_numbers(answer.value) == [0, 1, 2]
    request = model.requests[0]
    assert (request.role, request.thinking) == (ModelRole.PAGE, False)
    assert "MADE-UP TITLE" in request.text
    assert "<<<DATA name=lines>>>" in request.text
    assert answer.provenance.prompt_version == "page-layout.v1"


async def test_fails_after_one_repair_when_the_model_names_a_line_that_is_not_on_the_page() -> None:
    wrong = json.dumps({"blocks": [{"kind": "paragraph", "lines": [0, 99]}]})
    model = FakeModel(wrong, wrong)

    with pytest.raises(OutputInvalidError):
        await _service(model).read(caller(), PAGE, LINES)
    assert len(model.requests) == 2


async def test_reads_a_block_without_a_kind_as_a_paragraph_or_a_table() -> None:
    model = FakeModel(json.dumps({"blocks": [{"lines": [0]}, {"rows": [[[1], [2]]]}]}))

    answer = await _service(model).read(caller(), PAGE, LINES)

    assert [b.kind for b in answer.value.blocks] == ["paragraph", "table"]


def _max_items(schema: object) -> list[int]:
    if isinstance(schema, dict):
        own = [schema["maxItems"]] if "maxItems" in schema else []
        return own + [n for value in schema.values() for n in _max_items(value)]
    if isinstance(schema, list):
        return [n for value in schema for n in _max_items(value)]
    return []


@pytest.mark.parametrize(
    "model",
    [
        m
        for m in vars(answer_models).values()
        if isinstance(m, type) and issubclass(m, BaseModel) and m is not BaseModel
    ],
)
def test_every_answer_schema_lists_at_most_1000_items_when_llama_server_builds_its_grammar(
    model: type[BaseModel],
) -> None:
    assert max(_max_items(model.model_json_schema()), default=0) <= 1000  # more: "failed to parse grammar"
