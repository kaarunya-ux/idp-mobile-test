"""The designer: samples, suggestions, test runs, the publish gate, the mobile bundle and account deletion."""

from dataclasses import replace
from datetime import timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from extraction.core.config import Settings
from extraction.core.security import Principal, current_principal
from extraction.main import create_app
from extraction.pipeline.domain.standard_templates import PAN_CARD
from extraction.template.application.commands import AddSamplesCommand, VersionActionCommand
from extraction.template.application.services.designer_service import DesignerService
from extraction.template.application.services.template_service import TemplateService
from extraction.template.domain.definition_json import to_json
from extraction.template.domain.designer import (
    SampleFile,
    SampleStatus,
    TestRunStatus,
    definition_hash,
)
from extraction.template.domain.exceptions import (
    DefinitionInvalidError,
    SampleNotReadyError,
    SamplesMissingError,
    TemplateNotFoundError,
    TemplateNotTestedError,
    TestRunNotFoundError,
)
from extraction.template.domain.models import Template, TemplateStatus
from extraction.template.domain.ports import Suggestion
from support.template_fakes import ACCOUNT, NOW, ORG, STRANGER, Clock, Store, Unit

THRESHOLDS = {"quality": {"blurMin": 100.0}, "review": {"confidenceThreshold": 0.85}}


class Tasks:
    def __init__(self) -> None:
        self.samples: list[UUID] = []
        self.runs: list[UUID] = []

    async def prepare_sample(self, sample_id: UUID) -> None:
        self.samples.append(sample_id)

    async def run_test(self, run_id: UUID) -> None:
        self.runs.append(run_id)


class SampleStorage:
    def __init__(self) -> None:
        self.deleted: list[UUID] = []

    async def read_words(self, key: str) -> list[dict]:
        return [{"text": "Name", "box": [0.1, 0.2, 0.1, 0.05]}]

    async def delete_account(self, account_id: UUID) -> int:
        self.deleted.append(account_id)
        return 3


class Suggester:
    def __init__(self) -> None:
        self.asked: list[tuple[SampleFile, list, UUID | None]] = []

    async def suggest(self, page, words, account_id) -> Suggestion:
        self.asked.append((page, list(words), account_id))
        return Suggestion(fields=[{"key": "name"}], tables=[], model_id="vl", prompt_version="template-suggest.v1")


def _template(store: Store) -> Template:
    template = Template.create(
        id=UUID(int=10),
        account_id=ACCOUNT,
        document_type_code="PAN_CARD",
        name="My PAN",
        description=None,
        definition=to_json(PAN_CARD),
        actor="user-1",
        now=NOW,
    )
    store.templates[template.id] = template
    return template


def _designer(store: Store) -> tuple[DesignerService, Tasks, Suggester]:
    tasks, suggester = Tasks(), Suggester()
    service = DesignerService(
        lambda: Unit(store),
        Clock(),
        tasks=tasks,
        storage=SampleStorage(),
        suggester=suggester,
        bundle_thresholds=THRESHOLDS,
    )
    return service, tasks, suggester


def _files(key: str = f"{ACCOUNT}/doc/file-1") -> tuple[SampleFile, ...]:
    return (SampleFile(bucket="idp-originals", key=key),)


def _add(service: DesignerService, template: Template, *samples) -> AddSamplesCommand:
    return AddSamplesCommand(caller=ORG, template_id=template.id, version=1, samples=tuple(samples))


async def test_records_samples_as_pending_and_hands_each_to_a_worker() -> None:
    store = Store()
    template = _template(store)
    service, tasks, _ = _designer(store)

    added = await service.add_samples(_add(service, template, (_files(), {"pan_number": "ABCDE1234F"})))

    assert [s.status for s in added] == [SampleStatus.PENDING]
    assert tasks.samples == [added[0].id]
    assert (await service.list_samples(ORG, template.id, 1))[0].expected == {"pan_number": "ABCDE1234F"}


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        (_files(f"{UUID(int=99)}/doc/file-1"), {}),  # another account's upload
        ((SampleFile(bucket="idp-outputs", key=f"{ACCOUNT}/x"),), {}),  # a bucket samples don't come from
        (_files("../etc/passwd"), {}),
        (_files(), {"not_a_field": "x"}),
    ],
)
async def test_refuses_samples_from_elsewhere_or_with_unknown_fields(files, expected) -> None:
    store = Store()
    template = _template(store)
    service, _, _ = _designer(store)

    with pytest.raises(DefinitionInvalidError):
        await service.add_samples(_add(service, template, (files, expected)))


async def test_refuses_more_than_twenty_samples_per_version() -> None:
    store = Store()
    template = _template(store)
    service, _, _ = _designer(store)

    with pytest.raises(DefinitionInvalidError):
        await service.add_samples(_add(service, template, *[(_files(), {})] * 21))


async def test_hides_another_accounts_template_from_the_designer() -> None:
    store = Store()
    template = _template(store)
    service, _, _ = _designer(store)

    with pytest.raises(TemplateNotFoundError):
        await service.list_samples(STRANGER, template.id, 1)


async def _ready_sample(store: Store, service: DesignerService, template: Template, expected=None):
    (sample,) = await service.add_samples(_add(service, template, (_files(), expected or {"pan_number": "ABCDE1234F"})))
    stored = store.samples[sample.id]
    stored.status, stored.page_key, stored.words_key = SampleStatus.READY, "k/page-0.png", "k/words.json"
    return stored


async def test_asks_for_suggestions_with_the_prepared_page_and_words() -> None:
    store = Store()
    template = _template(store)
    service, _, suggester = _designer(store)
    await _ready_sample(store, service, template)

    found = await service.suggest(ORG, template.id, 1, None)

    page, words, account = suggester.asked[0]
    assert (page.key, words[0]["text"], account) == ("k/page-0.png", "Name", ACCOUNT)
    assert found.fields == [{"key": "name"}]


async def test_answers_409_when_the_sample_is_not_prepared() -> None:
    store = Store()
    template = _template(store)
    service, _, _ = _designer(store)
    await service.add_samples(_add(service, template, (_files(), {})))

    with pytest.raises(SampleNotReadyError):
        await service.suggest(ORG, template.id, 1, None)


async def test_snapshots_ready_samples_with_expected_values_when_a_test_run_starts() -> None:
    store = Store()
    template = _template(store)
    service, tasks, _ = _designer(store)
    sample = await _ready_sample(store, service, template)
    await service.add_samples(_add(service, template, (_files(), {"pan_number": "X"})))  # still pending: left out

    run = await service.start_test_run(ORG, template.id, 1)

    assert run.status is TestRunStatus.QUEUED
    assert run.samples == [{"sampleId": str(sample.id), "expected": {"pan_number": "ABCDE1234F"}}]
    assert run.definition_hash == definition_hash(template.version(1).definition)
    assert tasks.runs == [run.id]
    assert (await service.get_test_run(ORG, template.id, 1, run.id)).id == run.id
    with pytest.raises(TestRunNotFoundError):
        await service.get_test_run(ORG, template.id, 2, run.id)


async def test_needs_a_ready_sample_with_expected_values_to_start_a_test_run() -> None:
    store = Store()
    template = _template(store)
    service, _, _ = _designer(store)

    with pytest.raises(SamplesMissingError):
        await service.start_test_run(ORG, template.id, 1)


def _report(accuracy: float) -> dict:
    return {
        "fields": {"pan_number": {"required": True, "accuracy": accuracy}, "dob": {"required": False, "accuracy": 0.1}}
    }


async def _with_run(
    store: Store, template: Template, *, accuracy: float, definition: str | None = None, age=timedelta()
):
    service, _, _ = _designer(store)
    await _ready_sample(store, service, template)
    run = await service.start_test_run(ORG, template.id, 1)
    stored = store.runs[run.id]
    stored.status, stored.report, stored.finished_at = TestRunStatus.COMPLETED, _report(accuracy), NOW - age
    if definition is not None:
        stored.definition_hash = definition


def _publish(template: Template, reason: str | None = None) -> VersionActionCommand:
    return VersionActionCommand(
        caller=ORG, idempotency_key=f"key-{reason}", template_id=template.id, number=1, override_reason=reason
    )


async def test_publishes_without_a_reason_when_a_recent_test_run_passed() -> None:
    store = Store()
    template = _template(store)
    await _with_run(store, template, accuracy=0.95)

    published = await TemplateService(lambda: Unit(store), Clock()).publish(_publish(template))

    assert published.current_version == 1
    assert published.version(1).publish_reason is None


@pytest.mark.parametrize(
    "run",
    [
        {"accuracy": 0.8},  # a required field below 90 %
        {"accuracy": 0.95, "definition": "an-older-definition"},  # the draft changed after the run
        {"accuracy": 0.95, "age": timedelta(hours=25)},  # too old
    ],
)
async def test_needs_a_reason_when_the_last_test_run_does_not_count(run) -> None:
    store = Store()
    template = _template(store)
    await _with_run(store, template, **run)

    with pytest.raises(TemplateNotTestedError):
        await TemplateService(lambda: Unit(store), Clock()).publish(_publish(template))


async def test_bundles_published_templates_with_thresholds_and_a_content_version() -> None:
    store = Store()
    template = _template(store)
    draft_only = replace(_template(Store()), id=UUID(int=11), name="Draft only")
    store.templates[draft_only.id] = draft_only
    template.publish(1, actor="u", now=NOW, tested_at=None, override_reason="first version")
    service, _, _ = _designer(store)

    bundle = await service.bundle(ORG)
    again = await service.bundle(ORG)

    assert bundle.version == again.version
    assert [t["id"] for t in bundle.body["templates"]] == [str(template.id)]
    assert bundle.body["thresholds"] == THRESHOLDS
    assert "pan-format" in bundle.body["rules"]


async def test_archives_the_accounts_templates_and_types_when_it_is_deleted() -> None:
    store = Store()
    template = _template(store)

    archived = await TemplateService(lambda: Unit(store), Clock()).archive_account(ACCOUNT)

    assert archived == 1
    assert store.templates[template.id].status is TemplateStatus.ARCHIVED


# --- Over HTTP --------------------------------------------------------------------------------------------------

WRITER = Principal(
    actor_id="u1", tenant_id=ACCOUNT, permissions=frozenset({"template:read", "template:write"}), kind="user"
)


def _client(store: Store) -> TestClient:
    app = create_app(Clock(), Settings(_env_file=None))
    app.state.template_service = TemplateService(lambda: Unit(store), Clock())
    app.state.designer_service = _designer(store)[0]
    app.dependency_overrides[current_principal] = lambda: WRITER
    return TestClient(app)


def test_answers_304_when_the_phone_already_has_the_bundle() -> None:
    store = Store()
    _template(store).publish(1, actor="u", now=NOW, tested_at=None, override_reason="first version")
    client = _client(store)

    first = client.get("/api/v1/templates/bundle")
    by_etag = client.get("/api/v1/templates/bundle", headers={"If-None-Match": first.headers["ETag"]})
    by_since = client.get("/api/v1/templates/bundle", params={"since": first.json()["bundleVersion"]})

    assert first.status_code == 200
    assert first.json()["format"] == "idp-template-bundle.v1"
    assert (by_etag.status_code, by_since.status_code) == (304, 304)


def test_adds_samples_and_starts_a_test_run_over_http() -> None:
    store = Store()
    template = _template(store)
    client = _client(store)
    body = {"samples": [{"files": [{"bucket": "idp-originals", "key": f"{ACCOUNT}/d/f"}], "expected": {"name": "A"}}]}

    added = client.post(f"/api/v1/templates/{template.id}/versions/1/samples", json=body)
    listed = client.get(f"/api/v1/templates/{template.id}/versions/1/samples")
    no_run = client.post(f"/api/v1/templates/{template.id}/versions/1/test-runs")

    assert added.status_code == 201
    assert listed.json()["items"][0]["status"] == "PENDING"
    assert (no_run.status_code, no_run.json()["code"]) == (409, "EXT-409-SAMPLE-002")


def test_answers_409_for_suggestions_when_no_sample_is_prepared_with_the_real_wiring() -> None:
    store = Store()
    template = _template(store)
    app = create_app(Clock(), Settings(_env_file=None))
    app.state.template_service = TemplateService(lambda: Unit(store), Clock())
    app.state.designer_service._units = lambda: Unit(store)  # the real wiring, with no gateway or storage
    app.dependency_overrides[current_principal] = lambda: WRITER
    client = TestClient(app)

    response = client.post(f"/api/v1/templates/{template.id}/versions/1/suggestions", json={})

    assert response.status_code == 409  # no prepared sample yet: that is checked first
