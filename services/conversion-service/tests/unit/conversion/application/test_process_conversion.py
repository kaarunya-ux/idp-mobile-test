"""Processing: PREPARE -> CONVERT -> PACKAGE with progress, then a result or a failure Java can act on."""

import hashlib
import zipfile
from datetime import timedelta
from pathlib import Path

import pytest

from conversion.bootstrap import build_convert_file_service
from conversion.conversion.application.commands import ProcessItemCommand
from conversion.conversion.application.services.process_conversion import ProcessConversionService
from conversion.conversion.domain.events import ConversionCompleted, JobProgressed, WorkFailed
from conversion.conversion.domain.exceptions import UnexpectedFailureError
from conversion.conversion.domain.formats import Format
from conversion.conversion.domain.models import ConversionItem, ItemStatus
from conversion.conversion.domain.values import Stage
from conversion.core.config import Settings

STALE_AFTER = timedelta(minutes=12)


@pytest.fixture
def service(units, storage, inspector, clock) -> ProcessConversionService:
    return ProcessConversionService(
        units,
        storage,
        inspector,
        build_convert_file_service(Settings(_env_file=None)),
        clock,
        outputs_bucket="idp-outputs",
        stale_after=STALE_AFTER,
    )


@pytest.fixture
def queued(database, storage, conversion_request, clock, make_image, make_pdf):
    """``queued(source, target, ...)``: an item in the database, with its input files in storage."""

    def make(source=Format.PNG, target=Format.PDF, *, inputs=1, options=None, files=None):
        request = conversion_request(source, target, inputs=inputs, options=options)
        for number, file in enumerate(request.inputs):
            if files is not None:
                storage.put(file.ref, files[number])
            elif source is Format.PDF:
                storage.put(file.ref, make_pdf(f"in-{number}.pdf", sizes=[(200, 100)] * 3))
            else:
                storage.put(file.ref, make_image(f"in-{number}{source.suffix}"))
        database.items[request.context.item_id] = ConversionItem.receive(request, clock.now())
        return request.context.item_id

    return make


def test_converts_uploads_and_reports_progress_then_completion(service, database, storage, queued) -> None:
    item_id = queued(Format.PNG, Format.PDF)

    service.process(ProcessItemCommand(item_id=item_id))

    *progress, completed = database.events
    assert [(e.stage, e.progress, e.sequence) for e in progress if isinstance(e, JobProgressed)] == [
        (Stage.PREPARE, 0, 1),
        (Stage.CONVERT, 30, 2),
        (Stage.PACKAGE, 80, 3),
    ]
    assert isinstance(completed, ConversionCompleted)
    (output,) = completed.outputs
    stored = storage.root / "idp-outputs" / "acc/job/item/document.pdf"
    assert output.ref.key == "acc/job/item/document.pdf"
    assert (output.content_type, output.size, output.page_count) == ("application/pdf", stored.stat().st_size, 1)
    assert output.sha256 == hashlib.sha256(stored.read_bytes()).hexdigest()
    assert (completed.usage_pages, completed.sequence) == (1, 4)
    item = database.items[item_id]
    assert (item.status, item.attempts) == (ItemStatus.COMPLETED, 1)


def test_bundles_every_page_into_one_zip_when_the_output_option_is_zip(service, database, storage, queued) -> None:
    item_id = queued(Format.PDF, Format.PNG, options={"output": "ZIP"})

    service.process(ProcessItemCommand(item_id=item_id))

    completed = database.events[-1]
    assert isinstance(completed, ConversionCompleted)
    (output,) = completed.outputs
    assert (output.ref.key, output.content_type, output.page_count) == (
        "acc/job/item/document.zip",
        "application/zip",
        None,
    )
    with zipfile.ZipFile(storage.root / "idp-outputs" / output.ref.key) as bundle:
        assert bundle.namelist() == [f"document-page-000{n}.png" for n in (1, 2, 3)]


def test_keeps_one_output_per_page_by_default(service, database, queued) -> None:
    item_id = queued(Format.PDF, Format.JPEG, options={"pageRange": "1-2"})

    service.process(ProcessItemCommand(item_id=item_id))

    completed = database.events[-1]
    assert isinstance(completed, ConversionCompleted)
    assert [o.ref.key.rsplit("/", 1)[1] for o in completed.outputs] == [
        "document-page-0001.jpg",
        "document-page-0002.jpg",
    ]


def test_combines_several_images_into_one_pdf_named_after_the_first(service, database, queued) -> None:
    item_id = queued(Format.JPEG, Format.PDF, inputs=3)

    service.process(ProcessItemCommand(item_id=item_id))

    completed = database.events[-1]
    assert isinstance(completed, ConversionCompleted)
    assert [o.ref.key for o in completed.outputs] == ["acc/job/item/document.pdf"]
    assert completed.usage_pages == 3


@pytest.mark.parametrize(
    ("setup", "code", "retryable"),
    [
        ("damaged", "CNV-422-FILE-001", False),
        ("scanned", "CNV-422-ROUTE-001", False),  # needs OCR, which this build doesn't have
        ("single", "CNV-422-OPTIONS-001", False),  # SINGLE asked, three pages made
    ],
)
def test_fails_the_item_with_a_code_when_the_file_or_options_are_the_problem(
    service, database, queued, inspector, tmp_path: Path, setup, code, retryable
) -> None:
    if setup == "damaged":
        broken = tmp_path / "broken.png"
        broken.write_bytes(b"not an image")
        item_id = queued(Format.PNG, Format.PDF, files=[broken])
    elif setup == "scanned":
        inspector.scanned = True
        item_id = queued(Format.PDF, Format.DOCX)
    else:
        item_id = queued(Format.PDF, Format.PNG, options={"output": "SINGLE"})

    service.process(ProcessItemCommand(item_id=item_id))

    failed = database.events[-1]
    assert isinstance(failed, WorkFailed)
    assert (failed.code, failed.retryable) == (code, retryable)
    assert database.items[item_id].status is ItemStatus.FAILED


def test_puts_the_item_back_in_the_queue_and_raises_when_a_dependency_fails(service, database, storage, queued) -> None:
    item_id = queued()
    storage.fail_with = ConnectionError("MinIO unreachable")

    with pytest.raises(ConnectionError):
        service.process(ProcessItemCommand(item_id=item_id))

    assert database.items[item_id].status is ItemStatus.QUEUED  # so the task's retry can start it again


@pytest.mark.parametrize("state", ["missing", "running", "completed"])
def test_does_nothing_when_the_item_is_missing_already_running_or_finished(service, database, queued, state) -> None:
    item_id = queued()
    if state == "missing":
        del database.items[item_id]
    else:
        database.items[item_id].status = ItemStatus.RUNNING if state == "running" else ItemStatus.COMPLETED

    service.process(ProcessItemCommand(item_id=item_id))

    assert database.events == []


def test_takes_over_an_item_whose_worker_died(service, database, queued, clock) -> None:
    item_id = queued()
    database.items[item_id].status = ItemStatus.RUNNING
    clock.advance(timedelta(minutes=13))

    service.process(ProcessItemCommand(item_id=item_id))

    assert database.items[item_id].status is ItemStatus.COMPLETED


def test_reports_a_retryable_internal_error_when_the_task_gives_up(service, database, queued) -> None:
    item_id = queued()

    service.fail(item_id, UnexpectedFailureError())

    failed = database.events[-1]
    assert isinstance(failed, WorkFailed)
    assert (failed.code, failed.retryable) == ("CNV-500-INTERNAL-001", True)
