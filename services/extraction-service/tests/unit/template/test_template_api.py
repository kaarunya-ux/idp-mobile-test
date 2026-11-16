"""The templates API over HTTP: auth, status codes, headers and problem details (RTR-01..08, ERR-04)."""

import pytest
from fastapi.testclient import TestClient

from extraction.core.config import Settings
from extraction.core.security import Principal, current_principal
from extraction.main import create_app
from extraction.pipeline.domain.standard_templates import PAN_CARD
from extraction.template.application.services.document_type_service import DocumentTypeService
from extraction.template.application.services.template_service import TemplateService
from extraction.template.domain.definition_json import to_json
from support.template_fakes import ACCOUNT, Clock, Store, Unit, standard_types

ADMIN_USER = Principal(
    actor_id="u1",
    tenant_id=ACCOUNT,
    permissions=frozenset({"template:read", "template:write", "template:publish"}),
    kind="user",
)
MEMBER = Principal(actor_id="u2", tenant_id=ACCOUNT, permissions=frozenset({"template:read"}), kind="user")


def _client(principal: Principal | None, *, database: bool = True) -> TestClient:
    app = create_app(Clock(), Settings(_env_file=None))
    if database:
        store = Store(types=standard_types())
        app.state.template_service = TemplateService(lambda: Unit(store), Clock())
        app.state.document_type_service = DocumentTypeService(lambda: Unit(store))
    if principal is not None:
        app.dependency_overrides[current_principal] = lambda: principal
    return TestClient(app)


def _body(**changes: object) -> dict:
    return {"name": "My PAN", "documentType": "PAN_CARD", "definition": to_json(PAN_CARD), **changes}


def test_answers_401_without_a_token() -> None:
    response = _client(None).get("/api/v1/templates")

    assert response.status_code == 401
    assert response.json()["code"] == "EXT-401-AUTH-001"
    assert response.headers["content-type"] == "application/problem+json"


def test_answers_403_when_the_role_lacks_the_permission() -> None:
    response = _client(MEMBER).post("/api/v1/templates", json=_body(), headers={"Idempotency-Key": "key-00001"})

    assert (response.status_code, response.json()["code"]) == (403, "EXT-403-AUTH-001")


def test_creates_with_201_location_and_etag_then_reads_it_back() -> None:
    client = _client(ADMIN_USER)

    created = client.post("/api/v1/templates", json=_body(), headers={"Idempotency-Key": "key-00001"})
    template_id = created.json()["id"]
    fetched = client.get(f"/api/v1/templates/{template_id}")
    version = client.get(f"/api/v1/templates/{template_id}/versions/1")

    assert created.status_code == 201
    assert created.headers["Location"] == f"/api/v1/templates/{template_id}"
    assert (fetched.json()["scope"], fetched.headers["ETag"]) == ("ORG", '"1"')
    assert version.json()["definition"]["documentType"] == "PAN_CARD"


def test_needs_an_idempotency_key_on_create() -> None:
    response = _client(ADMIN_USER).post("/api/v1/templates", json=_body())

    assert (response.status_code, response.json()["code"]) == (400, "EXT-400-VALIDATION-001")


def test_lists_every_problem_of_a_bad_definition_with_422() -> None:
    bad = to_json(PAN_CARD) | {"schemaVersion": 3}
    bad["fields"][0]["type"] = "PHOTO"

    response = _client(ADMIN_USER).post(
        "/api/v1/templates", json=_body(definition=bad), headers={"Idempotency-Key": "key-00001"}
    )

    assert (response.status_code, response.json()["code"]) == (422, "EXT-422-DEFINITION-001")
    assert {"schemaVersion", "fields[0].type"} <= {e["field"] for e in response.json()["errors"]}


def test_answers_412_when_if_match_is_stale_and_409_when_publishing_without_a_reason() -> None:
    client = _client(ADMIN_USER)
    template_id = client.post("/api/v1/templates", json=_body(), headers={"Idempotency-Key": "key-00001"}).json()["id"]
    definition = {"definition": to_json(PAN_CARD)}

    ok = client.put(f"/api/v1/templates/{template_id}/versions/1", json=definition, headers={"If-Match": '"1"'})
    stale = client.put(f"/api/v1/templates/{template_id}/versions/1", json=definition, headers={"If-Match": '"1"'})
    untested = client.post(
        f"/api/v1/templates/{template_id}/versions/1/publish", json={}, headers={"Idempotency-Key": "key-pub-1"}
    )
    published = client.post(
        f"/api/v1/templates/{template_id}/versions/1/publish",
        json={"overrideReason": "checked"},
        headers={"Idempotency-Key": "key-pub-2"},
    )

    assert ok.status_code == 200
    assert (stale.status_code, stale.json()["code"]) == (412, "EXT-412-VERSION-001")
    assert (untested.status_code, untested.json()["code"]) == (409, "EXT-409-TEMPLATE-002")
    assert published.json()["currentVersion"] == 1


def test_lists_the_document_types_and_archives_with_204() -> None:
    client = _client(ADMIN_USER)
    template_id = client.post("/api/v1/templates", json=_body(), headers={"Idempotency-Key": "key-00001"}).json()["id"]

    types = client.get("/api/v1/document-types")
    archived = client.delete(f"/api/v1/templates/{template_id}")

    assert {t["code"] for t in types.json()["items"]} == {"PAN_CARD", "AADHAAR_CARD", "DRIVING_LICENCE"}
    assert archived.status_code == 204


@pytest.mark.parametrize("path", ["/api/v1/templates", "/api/v1/document-types"])
def test_answers_503_when_no_database_is_configured(path: str) -> None:
    response = _client(ADMIN_USER, database=False).get(path)

    assert (response.status_code, response.json()["code"]) == (503, "EXT-503-DEPENDENCY-001")
