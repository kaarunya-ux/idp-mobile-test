"""The internal API end to end on fake ports: token, body checks, both field-read forms, and problem details."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from ai_gateway.ai.application.services.analyse_page import AnalysePageService
from ai_gateway.ai.application.services.classify_page import ClassifyPageService
from ai_gateway.ai.application.services.read_fields import ReadFieldsService
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
