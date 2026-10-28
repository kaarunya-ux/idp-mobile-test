"""A stand-in pipeline (prepare and extract) and its fixed result, shared by the worker tests."""

import json
from datetime import timedelta

from extraction.pipeline.application.results import PrepareDocumentResult
from extraction.pipeline.application.services.run_extraction import RunExtractionService
from extraction.pipeline.domain.jobs import ExtractionItem
from extraction.pipeline.domain.models import PreparationSteps, PreparedPage
from extraction.pipeline.domain.quality import QualityScores
from extraction.pipeline.domain.stages import Stage
from idp_ocr_engines.model import PageImage
from support.job_fakes import Units, request

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


def page(index: int = 0) -> PreparedPage:
    steps = PreparationSteps(warped=False, deskew_degrees=0.0, rotation_degrees=0, orientation_confidence=None)
    return PreparedPage(page_index=index, clean=IMAGE, binarised=IMAGE, steps=steps)


class FakePrepare:
    def __init__(self, progress, *, error=None) -> None:
        self._progress, self._error = progress, error

    def prepare(self, command):
        if self._error is not None:
            raise self._error
        self._progress.report(Stage.PREPARE, 100)
        return PrepareDocumentResult(pages=(page(),), qualities=(QUALITY,))


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


def run_service(world, pipeline) -> RunExtractionService:
    database, storage, clock = world
    return RunExtractionService(
        Units(database), storage, storage, pipeline, clock, derived_bucket="idp-derived", stale_after=STALE_AFTER
    )


def queued(world, files: int = 1):
    database, storage, clock = world
    req = request(files=files)
    for ref in req.files:
        storage.objects[(ref.bucket, ref.key)] = (b"%PDF-fake", "application/pdf")
    database.items[req.context.item_id] = ExtractionItem.receive(req, clock.now())
    return req
