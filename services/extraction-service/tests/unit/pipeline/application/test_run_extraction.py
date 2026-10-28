"""The worker: SCAN ... ASSEMBLE with progress, pages and result.json in storage, then a result or a failure."""

import json

import pytest

from extraction.pipeline.application.commands import RunExtractionCommand
from extraction.pipeline.domain.exceptions import ImageBlurredError
from extraction.pipeline.domain.jobs import ExtractionCompleted, ItemStatus, JobProgressed, WorkFailed
from extraction.pipeline.domain.stages import Stage
from support.job_fakes import Clock, Database, Storage, request
from support.pipeline_fakes import (
    Pipeline,
    queued,
    run_service,
)


@pytest.fixture
def world():
    return Database(), Storage(), Clock()


def test_writes_pages_and_the_result_then_reports_completion_with_progress_in_order(world) -> None:
    database, storage, _ = world
    req = queued(world)

    run_service(world, Pipeline()).run(RunExtractionCommand(item_id=req.context.item_id))

    *progress, completed = database.events
    assert [(e.stage, e.progress) for e in progress] == [(Stage.SCAN, 0), (Stage.PREPARE, 100), (Stage.ASSEMBLE, 100)]
    assert [e.sequence for e in database.events] == [1, 2, 3, 4]
    assert isinstance(completed, ExtractionCompleted)
    base = f"{req.context.tenant_id}/{req.document_id}/{req.context.item_id}"
    assert (completed.result.bucket, completed.result.key) == ("idp-derived", f"{base}/result.json")
    assert (completed.document_type, completed.template_id, completed.template_version) == (
        "PAN_CARD",
        "standard-pan-card",
        1,
    )
    assert (completed.summary.fields, completed.summary.needs_review, completed.summary.avg_confidence) == (2, 1, 0.8)
    assert (completed.usage_pages, completed.usage_ai_calls) == (1, 1)
    stored, content_type = storage.objects[("idp-derived", f"{base}/result.json")]
    assert content_type == "application/json"
    assert json.loads(stored)["pages"][0]["imageKey"] == f"{base}/page-0.png"
    assert set(storage.pages) == {f"{base}/page-0.png"}
    assert database.items[req.context.item_id].status is ItemStatus.COMPLETED


def test_keeps_the_back_of_a_card_with_its_own_page_number(world) -> None:
    database, storage, _ = world
    req = queued(world, files=2)

    run_service(world, Pipeline()).run(RunExtractionCommand(item_id=req.context.item_id))

    base = f"{req.context.tenant_id}/{req.document_id}/{req.context.item_id}"
    assert set(storage.pages) == {f"{base}/page-0.png", f"{base}/page-1.png"}
    result = json.loads(storage.objects[("idp-derived", f"{base}/result.json")][0])
    assert [p["imageKey"] for p in result["pages"]] == [f"{base}/page-0.png", f"{base}/page-1.png"]
    assert database.events[-1].usage_pages == 2


def test_fails_with_the_quality_code_when_the_photo_is_blurred(world) -> None:
    database, _, _ = world
    req = queued(world)

    run_service(world, Pipeline(prepare_error=ImageBlurredError("blurred"))).run(
        RunExtractionCommand(item_id=req.context.item_id)
    )

    failed = database.events[-1]
    assert isinstance(failed, WorkFailed)
    assert (failed.code, failed.retryable) == ("EXT-422-QUALITY-001", False)
    assert database.items[req.context.item_id].status is ItemStatus.FAILED


def test_fails_with_ext_422_file_001_when_the_file_is_gone(world) -> None:
    database, storage, _ = world
    req = queued(world)
    storage.objects.clear()

    run_service(world, Pipeline()).run(RunExtractionCommand(item_id=req.context.item_id))

    assert database.events[-1].code == "EXT-422-FILE-001"


def test_puts_the_item_back_and_raises_when_something_unexpected_breaks(world) -> None:
    database, _, _ = world
    req = queued(world)

    with pytest.raises(RuntimeError, match="engine bug"):
        run_service(world, Pipeline(crash=True)).run(RunExtractionCommand(item_id=req.context.item_id))

    assert database.items[req.context.item_id].status is ItemStatus.QUEUED  # the task's retry starts it again


def test_does_nothing_when_the_item_is_running_elsewhere_or_unknown(world) -> None:
    database, _, clock = world
    req = queued(world)
    database.items[req.context.item_id].start(clock.now())
    service = run_service(world, Pipeline())

    service.run(RunExtractionCommand(item_id=req.context.item_id))
    service.run(RunExtractionCommand(item_id=request().context.item_id))

    assert database.events == []
    assert isinstance(JobProgressed, type)
