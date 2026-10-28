"""The mock model server: schema-shaped answers, recordings, and every gateway task working against it."""

import httpx
import pytest
from fastapi.testclient import TestClient

from ai_gateway.ai.application.runner import ModelCallRunner
from ai_gateway.ai.application.services.analyse_page import AnalysePageService
from ai_gateway.ai.application.services.classify_page import ClassifyPageService
from ai_gateway.ai.application.services.read_fields import ReadFieldsService
from ai_gateway.ai.application.services.suggest_template import SuggestTemplateService
from ai_gateway.ai.domain.models import FieldReadQuery, FieldSpec, ImageRef, ModelRole, Task
from ai_gateway.ai.infrastructure.breaker import CircuitBreaker
from ai_gateway.ai.infrastructure.model_client import OpenAiModelClient
from ai_gateway.ai.infrastructure.prompt_files import PromptFiles
from mock_model.main import MockSettings, create_app
from mock_model.recordings import RecordingError, load_recordings
from mock_model.schema_answer import answer_for_schema
from support.fakes import FakeCaps, FakeImages, FakeMetrics, FakeSlots, FixedClock, caller

PAGE = ImageRef(bucket="idp-derived", key="acct/doc/item/page-0.png")


def test_builds_the_smallest_answer_that_fits_a_schema() -> None:
    schema = {
        "type": "object",
        "properties": {
            "documentType": {"enum": ["PAN_CARD", None]},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "note": {"type": ["string", "null"]},
            "items": {"type": "array", "items": {"$ref": "#/$defs/Item"}, "minItems": 1},
            "pair": {"type": "array", "prefixItems": [{"type": "integer", "minimum": 2}, {"type": "boolean"}]},
            "optional": {"type": "string"},
        },
        "required": ["documentType", "confidence", "note", "items", "pair"],
        "$defs": {"Item": {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]}},
    }

    assert answer_for_schema(schema) == {
        "documentType": "PAN_CARD",
        "confidence": 0.5,
        "note": None,
        "items": [{"key": "mock"}],
        "pair": [2, False],
    }


def test_answers_like_an_openai_server_with_a_recording_when_one_matches() -> None:
    client = TestClient(create_app(MockSettings()))
    body = {
        "model": "vl",
        "messages": [{"role": "user", "content": [{"type": "text", "text": "This is an Indian PAN card."}]}],
        "response_format": {"type": "json_schema", "json_schema": {"name": "fields", "schema": {"type": "array"}}},
    }

    reply = client.post("/v1/chat/completions", json=body).json()

    assert reply["model"] == "mock-vl"
    assert reply["choices"][0]["message"]["content"].startswith('["ABCPE1234F"')
    assert reply["usage"]["prompt_tokens"] > 0
    assert client.get("/health/live").json() == {"status": "UP"}
    assert [m["id"] for m in client.get("/v1/models").json()["data"]] == ["vl", "smart", "auto"]


def test_refuses_a_malformed_recordings_folder(tmp_path) -> None:
    (tmp_path / "bad.json").write_text('[{"match": "no schema"}]')

    with pytest.raises(RecordingError):
        load_recordings(tmp_path)


def _runner() -> ModelCallRunner:
    transport = httpx.ASGITransport(app=create_app(MockSettings()))
    http = httpx.AsyncClient(transport=transport, base_url="http://mock-model/v1/")
    breaker = CircuitBreaker(failures=5, window_s=30, reset_after_s=30)
    model = OpenAiModelClient(http, models=dict.fromkeys(ModelRole, "vl"), breaker=breaker)
    return ModelCallRunner(
        model=model,
        slots=FakeSlots(),
        caps=FakeCaps(),
        prompts=PromptFiles.load(),
        metrics=FakeMetrics(),
        clock=FixedClock(),
        timeouts=dict.fromkeys(Task, 30.0),
    )


@pytest.mark.parametrize(
    ("document_type", "key", "value"),
    [("PAN_CARD", "pan_number", "ABCPE1234F"), ("AADHAAR_CARD", "gender", "FEMALE"), ("INVOICE", "total", None)],
)
async def test_every_card_and_a_generic_read_pass_the_gateways_checks_against_the_mock(
    document_type: str, key: str, value: str | None
) -> None:
    service = ReadFieldsService(runner=_runner(), images=FakeImages(), prompts=PromptFiles.load())
    query = FieldReadQuery(document_type=document_type, pages=[PAGE], fields=[FieldSpec(key=key, label=key)])

    answer = await service.read(caller(), query)

    assert answer.value[0].value == value
    assert answer.provenance.model == "mock-vl"


async def test_single_field_classify_analysis_and_suggestions_pass_the_gateways_checks_against_the_mock() -> None:
    runner, images, prompts = _runner(), FakeImages(), PromptFiles.load()

    single = await ReadFieldsService(runner=runner, images=images, prompts=prompts).read_one(
        caller(), PAGE, FieldSpec(key="name"), {"A": "TEST"}
    )
    classified = await ClassifyPageService(runner=runner, images=images, prompts=prompts).classify(
        caller(), PAGE, ["PAN_CARD", "AADHAAR_CARD"]
    )
    analysed = await AnalysePageService(runner=runner, images=images, prompts=prompts).analyse(
        caller(), [PAGE], {"fields": []}, None
    )
    suggested = await SuggestTemplateService(runner=runner, images=images, prompts=prompts).suggest(caller(), PAGE, [])

    assert single.value.value == "TEST VALUE"
    assert classified.value.document_type == "PAN_CARD"
    assert analysed.value.summary.startswith("A mock analysis")
    assert suggested.value.fields[0].key == "name"
