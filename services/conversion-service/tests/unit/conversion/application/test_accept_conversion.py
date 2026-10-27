"""Intake: each request is recorded once, mapped to an action by the item's state, and queued on the right pool."""

from datetime import timedelta
from uuid import uuid4

import pytest

from conversion.conversion.application.commands import AcceptConversionCommand, RejectConversionCommand
from conversion.conversion.application.services.accept_conversion import AcceptConversionService
from conversion.conversion.domain.events import ConversionCompleted, WorkFailed
from conversion.conversion.domain.exceptions import InvalidRequestError
from conversion.conversion.domain.formats import Format
from conversion.conversion.domain.models import ConversionRequest, IntakeAction, ItemStatus
from conversion.conversion.domain.route_planner import RoutePlanner
from conversion.conversion.domain.values import JobType, ObjectRef, StoredOutput, WorkerPool

STALE_AFTER = timedelta(minutes=12)


@pytest.fixture
def service(units, task_queue, clock) -> AcceptConversionService:
    return AcceptConversionService(units, task_queue, RoutePlanner(), clock, stale_after=STALE_AFTER)


def _command(request: ConversionRequest, message_id=None) -> AcceptConversionCommand:
    return AcceptConversionCommand(
        message_id=message_id or uuid4(), message_type="ConversionRequested", request=request
    )


@pytest.mark.parametrize(
    ("source", "target", "pool"),
    [
        (Format.PNG, Format.PDF, WorkerPool.PDF),
        (Format.DOCX, Format.PDF, WorkerPool.OFFICE),
        (Format.CSV, Format.DOCX, WorkerPool.DATA),
        (Format.CSV, Format.PNG, WorkerPool.OFFICE),  # CSV -> XLSX -> PDF -> PNG: LibreOffice is the scarce part
    ],
)
def test_records_a_new_item_and_queues_it_on_the_pool_its_route_needs(
    service, database, task_queue, conversion_request, source, target, pool
) -> None:
    request = conversion_request(source, target)

    action = service.accept(_command(request))

    assert action is IntakeAction.ENQUEUE
    assert task_queue.sent == [(request.context.item_id, pool)]
    assert database.items[request.context.item_id].status is ItemStatus.QUEUED


@pytest.mark.parametrize(
    ("job_type", "formats", "target", "pool"),
    [
        (JobType.PDF_MERGE, (Format.PDF, Format.PNG), Format.PDF, WorkerPool.PDF),
        (JobType.PREVIEW, (Format.PDF,), Format.JPEG, WorkerPool.PDF),
        (JobType.PREVIEW, (Format.PNG,), Format.JPEG, WorkerPool.PDF),
        (JobType.PREVIEW, (Format.DOCX,), Format.JPEG, WorkerPool.OFFICE),  # laid out by LibreOffice first
        (JobType.PDF_EDIT, (Format.PDF,), Format.PDF, WorkerPool.PDF),
        (JobType.EXPORT, (Format.JSON, Format.JSON), Format.CSV, WorkerPool.DATA),
        (JobType.EXPORT, (Format.JSON,), Format.PDF, WorkerPool.OFFICE),  # the report goes DOCX -> PDF
    ],
)
def test_queues_each_job_type_on_the_pool_its_work_needs(
    service, task_queue, conversion_request, job_type, formats, target, pool
) -> None:
    request = conversion_request(target=target, job_type=job_type, formats=formats)

    service.accept(_command(request))

    assert task_queue.sent == [(request.context.item_id, pool)]


def test_skips_a_redelivered_message_when_the_item_has_started(
    service, database, task_queue, conversion_request
) -> None:
    request, message_id = conversion_request(), uuid4()
    service.accept(_command(request, message_id))
    database.items[request.context.item_id].status = ItemStatus.RUNNING

    action = service.accept(_command(request, message_id))

    assert action is IntakeAction.SKIP
    assert len(task_queue.sent) == 1


def test_queues_again_when_a_redelivered_message_finds_the_item_still_queued(
    service, task_queue, conversion_request
) -> None:
    request, message_id = conversion_request(), uuid4()
    service.accept(_command(request, message_id))

    action = service.accept(_command(request, message_id))  # the first task may have been lost in a crash

    assert action is IntakeAction.ENQUEUE
    assert len(task_queue.sent) == 2


def test_sends_the_stored_result_again_when_a_completed_item_is_requested_again(
    service, database, task_queue, conversion_request, clock
) -> None:
    request = conversion_request()
    service.accept(_command(request))
    item = database.items[request.context.item_id]
    output = StoredOutput(
        ref=ObjectRef(bucket="idp-outputs", key="acc/job/item/document.pdf"),
        content_type="application/pdf",
        size=10,
        sha256="ab",
        page_count=1,
    )
    item.complete((output,), 1, clock.now())

    action = service.accept(_command(request))

    assert action is IntakeAction.RESEND_COMPLETION
    assert isinstance(database.events[-1], ConversionCompleted)
    assert database.events[-1].outputs == (output,)
    assert len(task_queue.sent) == 1  # not converted again


@pytest.mark.parametrize(
    ("status", "minutes_later", "expected"),
    [
        (ItemStatus.FAILED, 0, IntakeAction.ENQUEUE),  # Java retrying on purpose
        (ItemStatus.RUNNING, 1, IntakeAction.SKIP),  # a worker has it
        (ItemStatus.RUNNING, 13, IntakeAction.ENQUEUE),  # its worker died
    ],
)
def test_decides_from_the_item_state_when_a_new_request_arrives_for_a_known_item(
    service, database, task_queue, conversion_request, clock, status, minutes_later, expected
) -> None:
    request = conversion_request()
    service.accept(_command(request))
    database.items[request.context.item_id].status = status
    clock.advance(timedelta(minutes=minutes_later))

    action = service.accept(_command(request))

    assert action is expected
    assert len(task_queue.sent) == (2 if expected is IntakeAction.ENQUEUE else 1)


@pytest.mark.parametrize(
    ("target", "options", "code"),
    [
        (Format.PNG, {}, "CNV-422-ROUTE-001"),  # PNG -> PNG has no route
        (Format.PDF, {"dpi": 5000}, "CNV-422-OPTIONS-001"),
    ],
)
def test_fails_the_item_at_once_when_there_is_no_route_or_an_option_is_wrong(
    service, database, task_queue, conversion_request, target, options, code
) -> None:
    request = conversion_request(Format.PNG, target, options=options)

    action = service.accept(_command(request))

    assert action is IntakeAction.SKIP
    assert task_queue.sent == []
    (failed,) = database.events
    assert isinstance(failed, WorkFailed)
    assert (failed.code, failed.retryable) == (code, False)
    assert database.items[request.context.item_id].status is ItemStatus.FAILED


def test_reports_work_failed_once_when_a_request_is_rejected(service, database, conversion_request) -> None:
    request, message_id = conversion_request(), uuid4()
    command = RejectConversionCommand(
        message_id=message_id,
        message_type="ConversionRequested",
        item=request.context,
        error=InvalidRequestError("Input type text/html is not supported."),
    )

    service.reject(command)
    service.reject(command)  # a redelivery changes nothing

    (failed,) = database.events
    assert isinstance(failed, WorkFailed)
    assert (failed.code, failed.detail) == ("CNV-422-REQUEST-001", "Input type text/html is not supported.")
