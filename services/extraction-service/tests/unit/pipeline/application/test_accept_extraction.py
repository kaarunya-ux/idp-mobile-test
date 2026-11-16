"""Intake: each request is recorded once, mapped to an action by the item's state, and queued."""

from datetime import timedelta
from uuid import uuid4

import pytest

from extraction.pipeline.application.commands import AcceptExtractionCommand, RejectExtractionCommand
from extraction.pipeline.application.services.accept_extraction import AcceptExtractionService
from extraction.pipeline.domain.exceptions import RequestRejectedError
from extraction.pipeline.domain.jobs import (
    ExtractionItem,
    ExtractionOutcome,
    IntakeAction,
    ItemStatus,
    ObjectRef,
    ResultSummary,
    WorkFailed,
)
from support.job_fakes import Clock, Database, Tasks, Units, request

STALE_AFTER = timedelta(minutes=12)


@pytest.fixture
def world():
    database, tasks, clock = Database(), Tasks(), Clock()
    return database, tasks, clock, AcceptExtractionService(Units(database), tasks, clock, stale_after=STALE_AFTER)


def _command(req, message_id=None):
    return AcceptExtractionCommand(message_id=message_id or uuid4(), message_type="ExtractionRequested", request=req)


def test_records_a_new_item_and_queues_it(world) -> None:
    database, tasks, _, service = world
    req = request()

    assert service.accept(_command(req)) is IntakeAction.ENQUEUE

    assert tasks.sent == [req.context.item_id]
    assert database.items[req.context.item_id].status is ItemStatus.QUEUED


def test_skips_a_redelivered_message_once_the_item_has_started(world) -> None:
    database, tasks, clock, service = world
    req, message_id = request(), uuid4()
    service.accept(_command(req, message_id))
    database.items[req.context.item_id].start(clock.now())

    assert service.accept(_command(req, message_id)) is IntakeAction.SKIP
    assert tasks.sent == [req.context.item_id]


@pytest.mark.parametrize(
    ("status", "minutes_later", "expected"),
    [
        (ItemStatus.QUEUED, 0, IntakeAction.ENQUEUE),
        (ItemStatus.RUNNING, 1, IntakeAction.SKIP),
        (ItemStatus.RUNNING, 30, IntakeAction.ENQUEUE),  # its worker died
        (ItemStatus.FAILED, 0, IntakeAction.ENQUEUE),  # Java retries on purpose
    ],
)
def test_decides_from_the_item_state_when_a_new_request_arrives(world, status, minutes_later, expected) -> None:
    database, _, clock, service = world
    req = request()
    item = ExtractionItem.receive(req, clock.now())
    item.status = status
    database.items[item.id] = item
    clock.advance(timedelta(minutes=minutes_later))

    assert service.accept(_command(req)) is expected


def test_sends_the_stored_result_again_when_a_completed_item_is_requested_again(world) -> None:
    database, tasks, _, service = world
    req = request()
    service.accept(_command(req))
    item = database.items[req.context.item_id]
    outcome = ExtractionOutcome(
        result=ObjectRef(bucket="idp-derived", key="a/b/c/result.json"),
        document_type="PAN_CARD",
        template_id="standard-pan-card",
        template_version=1,
        summary=ResultSummary(fields=4, needs_review=0, avg_confidence=0.95),
        pages=1,
        ai_calls=0,
    )
    stored = item.complete(outcome, item.updated_at)

    assert service.accept(_command(req)) is IntakeAction.RESEND_COMPLETION
    assert database.events[-1] == stored
    assert tasks.sent == [req.context.item_id]


def test_reports_work_failed_once_when_a_request_is_rejected(world) -> None:
    database, _, _, service = world
    req, message_id = request(), uuid4()
    command = RejectExtractionCommand(
        message_id=message_id,
        message_type="ExtractionRequested",
        item=req.context,
        error=RequestRejectedError("bad"),
    )

    service.reject(command)
    service.reject(command)

    (failed,) = database.events
    assert isinstance(failed, WorkFailed)
    assert (failed.code, failed.retryable) == ("EXT-400-VALIDATION-001", False)
