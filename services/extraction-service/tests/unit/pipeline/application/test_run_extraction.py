"""The worker: SCAN ... ASSEMBLE with progress, pages and result.json in storage, then a result or a failure."""

import json
from datetime import timedelta

import pytest

from extraction.pipeline.application.commands import RunExtractionCommand
from extraction.pipeline.application.results import PrepareDocumentResult
from extraction.pipeline.application.services.run_extraction import RunExtractionService
from extraction.pipeline.domain.exceptions import ImageBlurredError
from extraction.pipeline.domain.jobs import ExtractionCompleted, ExtractionItem, ItemStatus, JobProgressed, WorkFailed
from extraction.pipeline.domain.models import PreparationSteps, PreparedPage
from extraction.pipeline.domain.quality import QualityScores
from extraction.pipeline.domain.stages import Stage
from idp_ocr_engines.model import PageImage
from support.job_fakes import Clock, Database, Storage, Units, request

STALE_AFTER = timedelta(minutes=12)
IMAGE = PageImage(width=4, height=3, rgb=bytes(4 * 3 * 3))
QUALITY = QualityScores(blur=500.0, brightness=0.6, glare=0.0, short_side_px=900, edges_found=True)
RESULT = {
    "schemaVersion": 1,
    "documentType": {"code": "PAN_CARD", "confidence": 0.97},
    "template": {"id": "standard-pan-card", "version": 1, "scope": "STANDARD"},
    "pages": [{"index": 0, "width": 4, "height": 3, "imageKey": None}],
    "fields": [{"key": "pan_number", "confidence": 0.9}, {"key": "name", "confidence": 0.7}],
    "validation": {"fieldsTotal": 2, "accepted": 1, "needsReview": 1, "failedRules": []},
    "provenance": {"models": [{"id": "vl", "promptVersion": "card-fields.v1"}]},
}


def _page(index: int = 0) -> PreparedPage:
    steps = PreparationSteps(warped=False, deskew_degrees=0.0, rotation_degrees=0, orientation_confidence=None)
    return PreparedPage(page_index=index, clean=IMAGE, binarised=IMAGE, steps=steps)


class FakePrepare:
    def __init__(self, progress, *, error=None) -> None:
        self._progress, self._error = progress, error

    def prepare(self, command):
        if self._error is not None:
            raise self._error
        self._progress.report(Stage.PREPARE, 100)
        return PrepareDocumentResult(pages=(_page(),), qualities=(QUALITY,))


class FakeExtract:
    def __init__(self, progress, *, crash: bool = False) -> None:
        self._progress, self._crash = progress, crash

    def extract(self, command):
        if self._crash:
            raise RuntimeError("an engine bug")
        self._progress.report(Stage.ASSEMBLE, 100)
        result = json.loads(json.dumps(RESULT))
        result["pages"] = [{"index": p.page_index, "imageKey": None} for p in command.pages[:1]]
        return result


class Pipeline:
    def __init__(self, *, prepare_error=None, crash: bool = False) -> None:
        self._prepare_error, self._crash = prepare_error, crash

    def prepare_service(self, progress):
        return FakePrepare(progress, error=self._prepare_error)

    def extract_service(self, progress, templates):
        self.templates = templates
        return FakeExtract(progress, crash=self._crash)

    class _Catalog:
        def templates_for(self, account_id, template_id):
            return ["the account's templates"]

    catalog = _Catalog()

    class _NoText:
        def read_first_page(self, content):
            return None

    digital_reader = _NoText()


@pytest.fixture
def world():
    return Database(), Storage(), Clock()


def _service(world, pipeline) -> RunExtractionService:
    database, storage, clock = world
    return RunExtractionService(
        Units(database), storage, storage, pipeline, clock, derived_bucket="idp-derived", stale_after=STALE_AFTER
    )


def _queued(world, files: int = 1):
    database, storage, clock = world
    req = request(files=files)
    for ref in req.files:
        storage.objects[(ref.bucket, ref.key)] = (b"%PDF-fake", "application/pdf")
    database.items[req.context.item_id] = ExtractionItem.receive(req, clock.now())
    return req


def test_writes_pages_and_the_result_then_reports_completion_with_progress_in_order(world) -> None:
    database, storage, _ = world
    req = _queued(world)

    _service(world, Pipeline()).run(RunExtractionCommand(item_id=req.context.item_id))

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
    req = _queued(world, files=2)

    _service(world, Pipeline()).run(RunExtractionCommand(item_id=req.context.item_id))

    base = f"{req.context.tenant_id}/{req.document_id}/{req.context.item_id}"
    assert set(storage.pages) == {f"{base}/page-0.png", f"{base}/page-1.png"}
    result = json.loads(storage.objects[("idp-derived", f"{base}/result.json")][0])
    assert [p["imageKey"] for p in result["pages"]] == [f"{base}/page-0.png", f"{base}/page-1.png"]
    assert database.events[-1].usage_pages == 2


def test_fails_with_the_quality_code_when_the_photo_is_blurred(world) -> None:
    database, _, _ = world
    req = _queued(world)

    _service(world, Pipeline(prepare_error=ImageBlurredError("blurred"))).run(
        RunExtractionCommand(item_id=req.context.item_id)
    )

    failed = database.events[-1]
    assert isinstance(failed, WorkFailed)
    assert (failed.code, failed.retryable) == ("EXT-422-QUALITY-001", False)
    assert database.items[req.context.item_id].status is ItemStatus.FAILED


def test_fails_with_ext_422_file_001_when_the_file_is_gone(world) -> None:
    database, storage, _ = world
    req = _queued(world)
    storage.objects.clear()

    _service(world, Pipeline()).run(RunExtractionCommand(item_id=req.context.item_id))

    assert database.events[-1].code == "EXT-422-FILE-001"


def test_puts_the_item_back_and_raises_when_something_unexpected_breaks(world) -> None:
    database, _, _ = world
    req = _queued(world)

    with pytest.raises(RuntimeError, match="engine bug"):
        _service(world, Pipeline(crash=True)).run(RunExtractionCommand(item_id=req.context.item_id))

    assert database.items[req.context.item_id].status is ItemStatus.QUEUED  # the task's retry starts it again


def test_does_nothing_when_the_item_is_running_elsewhere_or_unknown(world) -> None:
    database, _, clock = world
    req = _queued(world)
    database.items[req.context.item_id].start(clock.now())
    service = _service(world, Pipeline())

    service.run(RunExtractionCommand(item_id=req.context.item_id))
    service.run(RunExtractionCommand(item_id=request().context.item_id))

    assert database.events == []
    assert isinstance(JobProgressed, type)
