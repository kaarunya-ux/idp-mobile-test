"""generic-lines: the model names a document's fields by OCR line number, never copying text. Values are invented."""

import json

import pytest

from ai_gateway.ai.application.services.read_generic_lines import ReadGenericLinesService
from ai_gateway.ai.domain.exceptions import OutputInvalidError
from ai_gateway.ai.domain.models import ModelRole
from ai_gateway.ai.infrastructure.prompt_files import PromptFiles
from support.fakes import FakeModel, caller, runner

LINES = [(0, "MOCK TRADERS"), (1, "Email: demo@mock.example"), (2, "Item"), (3, "Qty"), (4, "Pens"), (5, "5")]


def _service(model: FakeModel) -> ReadGenericLinesService:
    return ReadGenericLinesService(runner=runner(model), prompts=PromptFiles.load())


async def test_returns_each_field_by_line_number_on_the_text_model_with_the_lines_as_data() -> None:
    answer = {
        "document_type": "Business Card",
        "document_confidence": 0.9,
        "fields": [
            {"key": "company", "label": "Company", "type": "string", "lines": [0]},
            {"key": "email", "label": "Email", "type": "string", "lines": [1]},
            {"key": "items", "label": "Items", "type": "table", "rows": [[[2], [3]], [[4], [5]]]},
        ],
    }
    model = FakeModel(json.dumps(answer))

    found = await _service(model).read(caller(), LINES)

    assert found.value.document_type == "business_card"
    assert [(f.key, f.lines) for f in found.value.fields] == [("company", [0]), ("email", [1]), ("items", [])]
    assert found.value.fields[2].rows == [[[2], [3]], [[4], [5]]]
    request = model.requests[0]
    assert (request.role, request.thinking, list(request.images)) == (ModelRole.SMART, False, [])
    assert "<<<DATA name=lines>>>" in request.text
    assert found.provenance.prompt_version == "generic-lines.v1"


async def test_keeps_only_the_first_field_with_each_key() -> None:
    fields = [
        {"key": "email", "label": "Email", "type": "string", "lines": [1]},
        {"key": "email", "label": "Email", "type": "string", "lines": [0]},
    ]
    model = FakeModel(json.dumps({"document_type": "card", "document_confidence": 0.8, "fields": fields}))

    found = await _service(model).read(caller(), LINES)

    assert [f.lines for f in found.value.fields] == [[1]]


async def test_fails_after_one_repair_when_the_model_names_a_line_that_is_not_on_the_document() -> None:
    wrong = json.dumps(
        {
            "document_type": "card",
            "document_confidence": 0.8,
            "fields": [{"key": "name", "label": "Name", "type": "string", "lines": [99]}],
        }
    )
    model = FakeModel(wrong, wrong)

    with pytest.raises(OutputInvalidError):
        await _service(model).read(caller(), LINES)
    assert len(model.requests) == 2
