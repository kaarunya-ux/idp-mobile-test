"""The internal API end to end on fake ports: token, body checks, both field-read forms, and problem details."""

import base64
import json
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from ai_gateway.ai.application.services.analyse_page import AnalysePageService
from ai_gateway.ai.application.services.classify_page import ClassifyPageService
from ai_gateway.ai.application.services.read_document import ReadDocumentService
from ai_gateway.ai.application.services.read_fields import ReadFieldsService
from ai_gateway.ai.application.services.read_layout import ReadLayoutService
from ai_gateway.ai.application.services.read_page import ReadPageService
from ai_gateway.ai.application.services.suggest_template import SuggestTemplateService
from ai_gateway.ai.infrastructure.prompt_files import PromptFiles
from ai_gateway.bootstrap import AiServices, Container, Unavailable
from ai_gateway.core.clock import Clock
from ai_gateway.core.config import Settings
from ai_gateway.core.errors import PermissionDeniedError, UnauthenticatedError
from ai_gateway.core.security import ServicePrincipal
from ai_gateway.main import create_app
from support.fakes import ACCOUNT, FakeCaps, FakeImages, FakeModel, FixedClock, runner

PAGE = {"bucket": "idp-derived", "key": "acct/doc/item/page-0.png"}
PAN_ANSWER = '["ABCDE1234F", "TEST PERSON", "TEST PARENT", "father", "01/01/1990"]'


class _Verifier:
    async def verify(self, token: str) -> ServicePrincipal:
        if token == "web-app":
            raise PermissionDeniedError("service:ai-gateway")
        if token != "good":
            raise UnauthenticatedError
        return ServicePrincipal(client_id="extraction-service")


def _client(model: FakeModel, caps: FakeCaps | None = None, *, available: bool = True) -> Iterator[TestClient]:
    def build(settings: Settings, clock: Clock) -> Container:
        if not available:
            return Container(ai=Unavailable())
        shared = runner(model, caps=caps)
        images, prompts = FakeImages(), PromptFiles.load()
        return Container(
            ai=AiServices(
                read_fields=ReadFieldsService(runner=shared, images=images, prompts=prompts),
                classify=ClassifyPageService(runner=shared, images=images, prompts=prompts),
                analyse=AnalysePageService(runner=shared, images=images, prompts=prompts),
                suggest=SuggestTemplateService(runner=shared, images=images, prompts=prompts),
                read_page=ReadPageService(runner=shared, images=images, prompts=prompts, max_side_px=2048),
                read_document=ReadDocumentService(runner=shared, images=images, prompts=prompts),
                read_layout=ReadLayoutService(runner=shared, images=images, prompts=prompts, max_side_px=2048),
            )
        )

    app = create_app(FixedClock(), Settings(_env_file=None), build)  # type: ignore[call-arg]  # init-only argument
    app.state.token_verifier = _Verifier()
    with TestClient(app, headers={"Authorization": "Bearer good"}) as client:
        yield client


@pytest.fixture
def model() -> FakeModel:
    return FakeModel()


@pytest.fixture
def client(model: FakeModel) -> Iterator[TestClient]:
    yield from _client(model)


def _batch(**changes: object) -> dict[str, object]:
    body: dict[str, object] = {
        "accountId": str(ACCOUNT),
        "priority": "PRIORITY",
        "documentType": "PAN_CARD",
        "pages": [PAGE],
        "fields": [{"key": "pan_number", "label": "PAN", "readings": {"A": "ABCDE1234F", "B": None}}],
    }
    body.update(changes)
    return body


def test_returns_values_and_provenance_when_reading_fields_in_batch(client: TestClient, model: FakeModel) -> None:
    model.answers.append(PAN_ANSWER)

    response = client.post("/internal/v1/ai/field-read", json=_batch())

    assert response.status_code == 200
    body = response.json()
    assert body["fields"] == [{"key": "pan_number", "value": "ABCDE1234F"}]
    assert body["promptVersion"] == "field-read.v1"
    assert body["tokens"] == {"in": 100, "out": 10}
    assert set(body) >= {"model", "latencyMs"}


def test_returns_value_confidence_and_reason_when_reading_one_crop(client: TestClient, model: FakeModel) -> None:
    model.answers.append('{"value": "TEST PERSON", "confidence": 0.7, "reason": "blurred"}')
    body = {"accountId": str(ACCOUNT), "crop": PAGE, "field": {"key": "name"}, "readings": {"A": "TEST PERS0N"}}

    response = client.post("/internal/v1/ai/field-read", json=body)

    assert response.status_code == 200
    assert (response.json()["value"], response.json()["confidence"]) == ("TEST PERSON", 0.7)


@pytest.mark.parametrize(
    "body",
    [
        _batch(crop=PAGE),  # both forms
        _batch(pages=None),  # neither complete
        _batch(pages=[{"bucket": "idp-derived", "key": "../etc/passwd"}]),
        _batch(pages=[PAGE, PAGE, PAGE]),
        _batch(fields=[{"key": "Bad Key"}]),
        _batch(fields=[{"key": "a"}, {"key": "a"}]),
        _batch(accountId="not-a-uuid"),
    ],
)
def test_answers_400_when_the_body_is_not_valid(client: TestClient, body: dict[str, object]) -> None:
    response = client.post("/internal/v1/ai/field-read", json=body)

    assert response.status_code == 400
    assert response.json()["code"] == "AIG-400-VALIDATION-001"


@pytest.mark.parametrize(("token", "status"), [("bad", 401), ("web-app", 403)])
def test_refuses_when_the_caller_has_no_valid_service_token(client: TestClient, token: str, status: int) -> None:
    response = client.post("/internal/v1/ai/field-read", json=_batch(), headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == status


def test_answers_401_when_there_is_no_token(client: TestClient) -> None:
    assert client.post("/internal/v1/ai/field-read", json=_batch(), headers={"Authorization": ""}).status_code == 401


def test_answers_429_with_the_cap_code_when_the_allowance_is_used(model: FakeModel) -> None:
    for client in _client(model, FakeCaps(left=0)):
        response = client.post("/internal/v1/ai/field-read", json=_batch())

        assert response.status_code == 429
        assert response.json()["code"] == "AIG-429-CAP-001"


def test_answers_502_when_the_model_answer_cannot_be_repaired(client: TestClient, model: FakeModel) -> None:
    model.answers += ["nope", "nope"]

    response = client.post("/internal/v1/ai/field-read", json=_batch())

    assert response.status_code == 502
    assert response.json()["code"] == "AIG-502-OUTPUT-001"


def test_answers_503_when_redis_or_storage_is_not_configured(model: FakeModel) -> None:
    for client in _client(model, available=False):
        response = client.post("/internal/v1/ai/field-read", json=_batch())

        assert response.status_code == 503
        assert response.json()["code"] == "AIG-503-DEPENDENCY-001"
        assert client.get("/health/ready").status_code == 503
        assert client.get("/health/live").status_code == 200


def test_returns_the_document_type_when_classifying(client: TestClient, model: FakeModel) -> None:
    model.answers.append('{"documentType": "AADHAAR_CARD", "confidence": 0.95}')
    body = {"accountId": str(ACCOUNT), "page": PAGE, "candidates": ["PAN_CARD", "AADHAAR_CARD"]}

    response = client.post("/internal/v1/ai/classify", json=body)

    assert response.json()["documentType"] == "AADHAAR_CARD"


def test_answers_400_when_classify_candidates_repeat(client: TestClient) -> None:
    body = {"accountId": str(ACCOUNT), "page": PAGE, "candidates": ["PAN_CARD", "PAN_CARD"]}

    assert client.post("/internal/v1/ai/classify", json=body).status_code == 400


def test_returns_missed_fields_when_analysing_a_page(client: TestClient, model: FakeModel) -> None:
    model.answers.append('{"fields": [], "summary": "A PAN card.", "warnings": ["glare"]}')
    body = {"accountId": str(ACCOUNT), "pages": [PAGE], "currentResult": {"fields": []}, "question": "Is it valid?"}

    response = client.post("/internal/v1/ai/page-analysis", json=body)

    assert response.json()["warnings"] == ["glare"]


def test_returns_suggested_fields_when_suggesting_a_template(client: TestClient, model: FakeModel) -> None:
    model.answers.append(
        '{"fields": [{"key": "name", "label": "Name", "type": "NAME", "box": [0.1, 0.2, 0.3, 0.04]}], "tables": []}'
    )
    body = {"accountId": str(ACCOUNT), "page": PAGE, "ocrWords": [{"text": "Name", "box": [0.1, 0.2, 0.05, 0.04]}]}

    response = client.post("/internal/v1/ai/template-suggest", json=body)

    assert response.json()["fields"][0] == {
        "key": "name",
        "label": "Name",
        "type": "NAME",
        "box": [0.1, 0.2, 0.3, 0.04],
        "rule": None,
    }


def test_serves_prometheus_metrics(client: TestClient) -> None:
    assert "ai_requests_total" in client.get("/metrics/").text


def test_returns_a_pages_blocks_in_camel_case_when_reading_a_page() -> None:
    answer = {
        "analysis": {"n_blocks": 2, "table_column_counts": [2]},
        "blocks": [
            {"kind": "heading", "text": "Invoice", "align": "center", "size": "large", "bold": True},
            {
                "kind": "table",
                "has_header": True,
                "n_data_rows": 1,
                "n_cols": 2,
                "header": ["A", "B"],
                "rows": [["1", "2"]],
            },
        ],
    }
    model = FakeModel(json.dumps(answer))
    for client in _client(model):
        response = client.post(
            "/internal/v1/ai/page-read",
            json={"accountId": str(ACCOUNT), "page": PAGE},
        )

        body = response.json()
        assert response.status_code == 200
        assert body["analysis"] == {"nBlocks": 2, "tableColumnCounts": [2]}
        assert body["blocks"][1]["hasHeader"] is True
        assert body["promptVersion"] == "page-read.v1"


def test_returns_the_type_and_every_field_when_reading_any_document(client: TestClient, model: FakeModel) -> None:
    table = {"headers": ["Item", "Qty"], "rows": [["Paper", "2"]]}
    model.answers.append(
        json.dumps(
            {
                "document_type": "Tax Invoice",
                "document_confidence": 0.9,
                "fields": [
                    {
                        "key": "invoice_number",
                        "label": "Invoice No",
                        "type": "string",
                        "value": "INV-1",
                        "confidence": 0.9,
                    },
                    {"key": "line_items", "label": "Line Items", "type": "table", "value": table, "confidence": 0.8},
                ],
            }
        )
    )
    body = {"accountId": str(ACCOUNT), "priority": "INTERACTIVE", "pages": [PAGE, PAGE]}

    response = client.post("/internal/v1/ai/generic-read", json=body)

    found = response.json()
    assert (found["documentType"], found["documentConfidence"]) == ("tax_invoice", 0.9)
    assert [f["key"] for f in found["fields"]] == ["invoice_number", "line_items"]
    assert found["fields"][1]["value"] == table
    assert found["promptVersion"] == "generic-read.v1"


def test_answers_400_when_generic_read_has_more_than_two_pages(client: TestClient) -> None:
    body = {"accountId": str(ACCOUNT), "pages": [PAGE, PAGE, PAGE]}

    assert client.post("/internal/v1/ai/generic-read", json=body).status_code == 400


def test_reads_fields_from_an_image_sent_in_the_request(client: TestClient, model: FakeModel) -> None:
    model.answers.append(PAN_ANSWER)
    body = {
        "accountId": str(ACCOUNT),
        "documentType": "PAN_CARD",
        "pages": [{"data": base64.b64encode(b"jpeg bytes").decode()}],
        "fields": [{"key": "pan_number"}],
    }

    assert client.post("/internal/v1/ai/field-read", json=body).status_code == 200


@pytest.mark.parametrize(
    "image",
    [{"bucket": "idp-derived", "key": "k", "data": "AAAA"}, {"data": "not base64!"}, {"bucket": "idp-derived"}, {}],
)
def test_answers_400_when_an_image_is_not_exactly_one_of_stored_or_sent(client: TestClient, image: dict) -> None:
    body = {"accountId": str(ACCOUNT), "page": image, "candidates": ["PAN_CARD"]}

    assert client.post("/internal/v1/ai/classify", json=body).status_code == 400


def test_returns_a_pages_structure_by_line_number(client: TestClient, model: FakeModel) -> None:
    layout = {
        "blocks": [
            {"kind": "heading", "lines": [0], "align": "center", "size": "large", "bold": True},
            {"kind": "table", "has_header": True, "rows": [[[1], [2]], [[3], []]]},
        ]
    }
    model.answers.append(json.dumps(layout))
    lines = [
        {"n": n, "text": t, "box": [0.1, 0.1 * (n + 1), 0.3, 0.04]} for n, t in enumerate(["TITLE", "A", "B", "1"])
    ]
    body = {"accountId": str(ACCOUNT), "page": {"data": base64.b64encode(b"jpeg").decode()}, "lines": lines}

    response = client.post("/internal/v1/ai/page-layout", json=body)

    assert response.status_code == 200
    heading, table = response.json()["blocks"]
    assert (heading["kind"], heading["lines"], heading["align"]) == ("heading", [0], "center")
    assert (table["kind"], table["hasHeader"], table["rows"]) == ("table", True, [[[1], [2]], [[3], []]])


def test_lays_out_a_page_from_its_lines_alone_when_no_page_is_sent(client: TestClient, model: FakeModel) -> None:
    model.answers.append(json.dumps({"blocks": [{"kind": "paragraph", "lines": [0]}]}))
    body = {"accountId": str(ACCOUNT), "lines": [{"n": 0, "text": "A", "box": [0.1, 0.1, 0.3, 0.04]}]}

    response = client.post("/internal/v1/ai/page-layout", json=body)

    assert response.status_code == 200
    assert response.json()["blocks"][0]["lines"] == [0]


def test_answers_400_when_page_layout_lines_repeat_a_number(client: TestClient) -> None:
    lines = [{"n": 0, "text": "A", "box": [0, 0, 0.1, 0.1]}, {"n": 0, "text": "B", "box": [0, 0.2, 0.1, 0.1]}]
    body = {"accountId": str(ACCOUNT), "page": {"data": base64.b64encode(b"jpeg").decode()}, "lines": lines}

    assert client.post("/internal/v1/ai/page-layout", json=body).status_code == 400
