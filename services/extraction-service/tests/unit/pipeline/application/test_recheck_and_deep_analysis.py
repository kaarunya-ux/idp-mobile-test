"""RECHECK (the device's weak fields read again on the server) and DEEP_ANALYSIS (the stored result to the AI)."""

import json
from dataclasses import replace
from datetime import timedelta

import pytest

from extraction.pipeline.application.commands import RunExtractionCommand
from extraction.pipeline.application.services.run_deep_analysis import RunDeepAnalysisService
from extraction.pipeline.domain.exceptions import AiUnavailableError
from extraction.pipeline.domain.jobs import (
    DeepAnalysisSpec,
    ExtractionCompleted,
    ExtractionItem,
    ExtractionRequest,
    JobKind,
    ObjectRef,
    RecheckSpec,
    WorkFailed,
)
from extraction.pipeline.domain.ports import AiAnalysis
from extraction.pipeline.domain.stages import Stage
from support.job_fakes import Clock, Database, Storage, Units, request
from support.pipeline_fakes import STALE_AFTER, Pipeline, queued, run_service


@pytest.fixture
def world():
    return Database(), Storage(), Clock()


ON_DEVICE = {
    "documentType": {"code": "PAN_CARD", "confidence": 0.9},
    "fields": [
        {"key": "pan_number", "value": "ABCDE1234F", "status": "ACCEPTED", "confidence": 0.95},
        {"key": "name", "value": "TEST PERSON", "status": "NEEDS_REVIEW", "confidence": 0.4},
    ],
}


def test_keeps_settled_device_fields_and_takes_the_server_reading_for_weak_ones_when_rechecking(world) -> None:
    database, storage, _ = world
    req = queued(world)
    on_device = ObjectRef(bucket="idp-derived", key=f"{req.context.tenant_id}/{req.document_id}/device/result.json")
    storage.objects[(on_device.bucket, on_device.key)] = (json.dumps(ON_DEVICE).encode(), "application/json")
    recheck = replace(req, kind=JobKind.RECHECK, recheck=RecheckSpec(result=on_device))
    database.items[req.context.item_id] = ExtractionItem.receive(recheck, world[2].now())

    run_service(world, Pipeline()).run(RunExtractionCommand(item_id=req.context.item_id))

    completed = database.events[-1]
    assert isinstance(completed, ExtractionCompleted)
    stored = json.loads(storage.objects[(completed.result.bucket, completed.result.key)][0])
    by_key = {f["key"]: f for f in stored["fields"]}
    assert by_key["pan_number"]["value"] == "ABCDE1234F"  # the device's, untouched
    assert by_key["name"] == {"key": "name", "confidence": 0.7}  # the server's reading replaced the weak one
    assert stored["recheck"]["checked"] == ["name"]
    assert database.runs[0].kind is JobKind.RECHECK


def test_records_a_pipeline_run_with_stage_timings_and_decision_counts(world) -> None:
    database, _, _ = world
    req = queued(world)

    run_service(world, Pipeline()).run(RunExtractionCommand(item_id=req.context.item_id))

    (run,) = database.runs
    assert run.item_id == req.context.item_id
    assert set(run.stages) == {Stage.PREPARE.value, Stage.ASSEMBLE.value}
    assert run.decisions == {"NONE/None": 2}
    assert (run.document_type, run.ai_calls) == ("PAN_CARD", 1)


class FakeAnalyser:
    def __init__(self, *, down: bool = False) -> None:
        self.down, self.calls = down, []

    def analyse(self, pages, current, question, context):
        self.calls.append((list(pages), current, question, context))
        if self.down:
            raise AiUnavailableError("down")
        return AiAnalysis(
            fields=[{"key": "pan_number", "value": "WRONG"}, {"key": "pincode", "label": "PIN", "value": "560001"}],
            summary="A PAN card.",
            warnings=["glare"],
            model_id="auto",
            prompt_version="page-analysis.v1",
        )


def _deep_item(world) -> ExtractionRequest:
    database, storage, clock = world
    base = request()
    tenant, document = base.context.tenant_id, base.document_id
    result = ObjectRef(bucket="idp-derived", key=f"{tenant}/{document}/first/result.json")
    storage.objects[(result.bucket, result.key)] = (json.dumps(ON_DEVICE).encode(), "application/json")
    spec = DeepAnalysisSpec(
        result=result,
        pages=(ObjectRef(bucket="idp-derived", key=f"{tenant}/{document}/first/page-0.png"),),
        question="Is it valid?",
    )
    req = replace(base, files=(), kind=JobKind.DEEP_ANALYSIS, analysis=spec)
    database.items[req.context.item_id] = ExtractionItem.receive(req, clock.now())
    return req


def _deep_service(world, analyser) -> RunDeepAnalysisService:
    database, storage, clock = world
    return RunDeepAnalysisService(
        Units(database), storage, analyser, clock, derived_bucket="idp-derived", stale_after=STALE_AFTER
    )


def test_merges_the_analysis_without_touching_accepted_fields_and_counts_one_ai_call(world) -> None:
    database, storage, _ = world
    req = _deep_item(world)
    analyser = FakeAnalyser()

    _deep_service(world, analyser).run(RunExtractionCommand(item_id=req.context.item_id))

    completed = database.events[-1]
    assert isinstance(completed, ExtractionCompleted)
    assert completed.result.key == f"{req.context.tenant_id}/{req.document_id}/{req.context.item_id}/result.json"
    assert completed.usage_ai_calls == 1
    stored = json.loads(storage.objects[(completed.result.bucket, completed.result.key)][0])
    assert stored["fields"][0]["value"] == "ABCDE1234F"
    assert [f["key"] for f in stored["analysis"]["fields"]] == ["pincode"]
    assert stored["analysis"]["summary"] == "A PAN card."
    sent = analyser.calls[0][1]
    assert sent["fields"][0] == {"key": "pan_number", "label": None, "value": "ABCDE1234F", "status": "ACCEPTED"}


def test_fails_retryable_when_the_ai_does_not_answer_deep_analysis(world) -> None:
    database, _, _ = world
    req = _deep_item(world)

    _deep_service(world, FakeAnalyser(down=True)).run(RunExtractionCommand(item_id=req.context.item_id))

    failed = database.events[-1]
    assert isinstance(failed, WorkFailed)
    assert (failed.code, failed.retryable) == ("EXT-503-AI-001", True)


def test_fails_retryable_when_no_gateway_is_configured(world) -> None:
    database, _, _ = world
    req = _deep_item(world)

    _deep_service(world, None).run(RunExtractionCommand(item_id=req.context.item_id))

    assert database.events[-1].code == "EXT-503-AI-001"


def test_does_nothing_when_the_deep_analysis_item_is_notqueued(world) -> None:
    database, _, clock = world
    req = _deep_item(world)
    database.items[req.context.item_id].start(clock.now())
    clock.advance(timedelta(minutes=1))

    _deep_service(world, FakeAnalyser()).run(RunExtractionCommand(item_id=req.context.item_id))

    assert database.events == []
