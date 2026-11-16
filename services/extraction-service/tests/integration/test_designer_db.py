"""The designer on real PostgreSQL (migration 0003): samples and test runs through the API's async repositories,
then the worker's sync store, then the publish gate reading the finished run."""

from uuid import UUID

import pytest

from extraction.core.clock import SystemClock
from extraction.pipeline.domain.standard_templates import PAN_CARD
from extraction.pipeline.infrastructure.persistence.designer_store import SqlDesignerWorkStore
from extraction.seed import seed
from extraction.template.application.commands import AddSamplesCommand, CreateTemplateCommand, VersionActionCommand
from extraction.template.application.services.designer_service import DesignerService
from extraction.template.application.services.template_service import TemplateService
from extraction.template.domain.definition_json import to_json
from extraction.template.domain.designer import SampleFile, SampleStatus, TestRunStatus
from extraction.template.infrastructure.repository import SqlTemplateUnitOfWork
from support.template_fakes import ACCOUNT, ORG, Clock

pytestmark = pytest.mark.integration


class Tasks:
    def __init__(self) -> None:
        self.samples: list[UUID] = []
        self.runs: list[UUID] = []

    async def prepare_sample(self, sample_id: UUID) -> None:
        self.samples.append(sample_id)

    async def run_test(self, run_id: UUID) -> None:
        self.runs.append(run_id)


class Unused:
    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"not used here: {name}")


async def test_runs_the_designer_flow_from_samples_to_a_tested_publish(async_sessions, sessions) -> None:
    seed(sessions)
    units = lambda: SqlTemplateUnitOfWork(async_sessions, Clock())  # noqa: E731
    templates = TemplateService(units, Clock())
    tasks = Tasks()
    designer = DesignerService(units, Clock(), tasks=tasks, storage=Unused(), suggester=Unused(), bundle_thresholds={})
    created = await templates.create(
        CreateTemplateCommand(
            caller=ORG,
            idempotency_key="designer-1",
            standard=False,
            name="My PAN",
            description=None,
            document_type="PAN_CARD",
            definition=to_json(PAN_CARD),
        )
    )
    files = (SampleFile(bucket="idp-originals", key=f"{ACCOUNT}/doc/file-1"),)
    (sample,) = await designer.add_samples(
        AddSamplesCommand(
            caller=ORG, template_id=created.id, version=1, samples=((files, {"pan_number": "ABCDE1234F"}),)
        )
    )
    store = SqlDesignerWorkStore(sessions, SystemClock())

    work = store.pending_sample(sample.id)
    store.sample_ready(sample.id, page_key="p.png", words_key="w.json")
    assert work is not None
    assert work.account_id == ACCOUNT
    assert store.pending_sample(sample.id) is None  # prepared: not again
    assert (await designer.list_samples(ORG, created.id, 1))[0].status is SampleStatus.READY

    run = await designer.start_test_run(ORG, created.id, 1)
    claimed = store.start_test_run(run.id)
    assert claimed is not None
    assert claimed.samples[0].expected == {"pan_number": "ABCDE1234F"}
    assert store.start_test_run(run.id) is None  # RUNNING: a second worker gets nothing
    report = {"samples": 1, "accuracy": 1.0, "fields": {"pan_number": {"required": True, "accuracy": 1.0}}}
    store.finish_test_run(run.id, report)

    finished = await designer.get_test_run(ORG, created.id, 1, run.id)
    published = await templates.publish(
        VersionActionCommand(caller=ORG, idempotency_key="designer-2", template_id=created.id, number=1)
    )
    assert finished.status is TestRunStatus.COMPLETED
    assert published.current_version == 1
    assert published.version(1).accuracy == {"testRunId": str(run.id), "accuracy": 1.0, "fields": {"pan_number": 1.0}}
