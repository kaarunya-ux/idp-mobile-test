"""Processing: PREPARE -> CONVERT -> PACKAGE with progress, then a result or a failure Java can act on."""

import hashlib
import json
import shutil
import zipfile
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pikepdf
import pytest
from PIL import Image

from conversion.bootstrap import build_job_runner
from conversion.conversion.application.commands import ProcessItemCommand
from conversion.conversion.application.services.process_conversion import OutputBuckets, ProcessConversionService
from conversion.conversion.domain.events import ConversionCompleted, ItemContext, JobProgressed, WorkFailed
from conversion.conversion.domain.exceptions import UnexpectedFailureError
from conversion.conversion.domain.formats import Format
from conversion.conversion.domain.models import ConversionItem, ConversionRequest, ItemStatus
from conversion.conversion.domain.values import InputFile, JobType, ObjectRef, Stage
from conversion.core.config import Settings

STALE_AFTER = timedelta(minutes=12)
needs_libreoffice = pytest.mark.skipif(shutil.which("soffice") is None, reason="LibreOffice is not installed")


@pytest.fixture
def service(units, storage, inspector, clock) -> ProcessConversionService:
    return ProcessConversionService(
        units,
        storage,
        inspector,
        build_job_runner(Settings(_env_file=None), storage),
        clock,
        buckets=OutputBuckets(outputs="idp-outputs", derived="idp-derived"),
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


@pytest.fixture
def queued_job(database, storage, conversion_request, clock):
    """``queued_job(job_type, target, files={Format.PDF: path, ...})``: an item of any job type, files in storage."""

    def make(job_type, target, files):
        request = conversion_request(target=target, job_type=job_type, formats=tuple(fmt for fmt, _ in files))
        for file, (_, path) in zip(request.inputs, files, strict=True):
            storage.put(file.ref, path)
        database.items[request.context.item_id] = ConversionItem.receive(request, clock.now())
        return request.context.item_id

    return make


def _outputs(database) -> ConversionCompleted:
    completed = database.events[-1]
    assert isinstance(completed, ConversionCompleted), completed
    return completed


def test_merges_pdfs_and_images_in_order_into_one_pdf_when_the_job_is_a_merge(
    service, database, storage, queued_job, make_pdf, make_image
) -> None:
    files = [
        (Format.PDF, make_pdf("a.pdf", sizes=[(200, 100)] * 2)),
        (Format.PNG, make_image("b.png", size=(60, 30), dpi=(72, 72))),
        (Format.PDF, make_pdf("c.pdf", sizes=[(100, 300)])),
    ]

    service.process(ProcessItemCommand(item_id=queued_job(JobType.PDF_MERGE, Format.PDF, files)))

    completed = _outputs(database)
    (output,) = completed.outputs
    assert (output.ref.bucket, output.ref.key, output.content_type) == (
        "idp-outputs",
        "acc/job/item/merged.pdf",
        "application/pdf",
    )
    assert completed.usage_pages == 4
    with pikepdf.open(storage.root / output.ref.bucket / output.ref.key) as pdf:
        sizes = [(round(float(p.mediabox[2])), round(float(p.mediabox[3]))) for p in pdf.pages]
    assert sizes == [(200, 100), (200, 100), (60, 30), (100, 300)]


def test_fails_with_cnv_422_pdf_001_when_a_pdf_to_merge_is_locked(service, database, queued_job, make_pdf) -> None:
    files = [
        (Format.PDF, make_pdf("a.pdf", sizes=[(200, 100)])),
        (Format.PDF, make_pdf("b.pdf", sizes=[(9, 9)], password="x")),
    ]

    service.process(ProcessItemCommand(item_id=queued_job(JobType.PDF_MERGE, Format.PDF, files)))

    failed = database.events[-1]
    assert isinstance(failed, WorkFailed)
    assert (failed.code, failed.retryable) == ("CNV-422-PDF-001", False)


def test_writes_one_small_jpeg_per_page_to_the_derived_bucket_when_the_job_is_a_pdf_preview(
    service, database, storage, queued_job, make_pdf
) -> None:
    files = [(Format.PDF, make_pdf("doc.pdf", sizes=[(595, 842), (842, 595), (595, 842)]))]

    service.process(ProcessItemCommand(item_id=queued_job(JobType.PREVIEW, Format.JPEG, files)))

    completed = _outputs(database)
    assert completed.usage_pages == 3
    assert [o.ref.key for o in completed.outputs] == [f"acc/job/item/document-page-000{n}.jpg" for n in (1, 2, 3)]
    assert {(o.ref.bucket, o.content_type) for o in completed.outputs} == {("idp-derived", "image/jpeg")}
    sizes = []
    for output in completed.outputs:
        with Image.open(storage.root / output.ref.bucket / output.ref.key) as image:
            sizes.append(image.size)
    assert sizes == [(283, 400), (400, 283), (283, 400)]


def test_writes_one_jpeg_thumbnail_when_the_preview_is_of_a_photo(
    service, database, storage, queued_job, make_image
) -> None:
    files = [(Format.JPEG, make_image("photo.jpg", size=(1600, 800)))]

    service.process(ProcessItemCommand(item_id=queued_job(JobType.PREVIEW, Format.JPEG, files)))

    (output,) = _outputs(database).outputs
    assert (output.ref.bucket, output.ref.key) == ("idp-derived", "acc/job/item/document-page-0001.jpg")
    with Image.open(storage.root / output.ref.bucket / output.ref.key) as image:
        assert (image.size, image.format) == ((400, 200), "JPEG")


def _queue_request(database, storage, clock, request, files) -> object:
    for file, path in zip(request.inputs, files, strict=True):
        storage.put(file.ref, path)
    database.items[request.context.item_id] = ConversionItem.receive(request, clock.now())
    return request.context.item_id


def _request(job_type, target, keys_and_formats, options):
    context = ItemContext(tenant_id=uuid4(), correlation_id="corr", job_id=uuid4(), item_id=uuid4())
    inputs = tuple(
        InputFile(ref=ObjectRef(bucket="idp-originals", key=key), format=fmt) for key, fmt in keys_and_formats
    )
    return ConversionRequest(
        context=context, inputs=inputs, target=target, options=options, output_prefix="acc/job/item", job_type=job_type
    )


def test_applies_the_edit_plan_with_pages_from_another_document_when_the_job_is_an_edit(
    service, database, storage, clock, make_text_pdf
) -> None:
    plan = {
        "schemaVersion": 1,
        "pages": [{"source": {"documentId": "doc-2", "page": 1}}, {"source": {"documentId": "self", "page": 1}}],
        "operations": [{"type": "PAGE_NUMBERS", "format": "{n}/{total}"}],
    }
    request = _request(
        JobType.PDF_EDIT,
        Format.PDF,
        [("acc/doc-1/file", Format.PDF), ("acc/doc-2/file", Format.PDF)],
        {"editPlan": plan},
    )
    files = [make_text_pdf("own.pdf", ["own page"]), make_text_pdf("other.pdf", ["other page"])]

    service.process(ProcessItemCommand(item_id=_queue_request(database, storage, clock, request, files)))

    completed = _outputs(database)
    (output,) = completed.outputs
    assert (output.ref.key, completed.usage_pages) == ("acc/job/item/edited.pdf", 2)
    with pikepdf.open(storage.root / output.ref.bucket / output.ref.key) as pdf:
        assert len(pdf.pages) == 2


def test_fails_with_cnv_422_plan_001_when_a_plan_needs_passwords_and_no_key_is_set(
    service, database, storage, clock, make_text_pdf
) -> None:
    plan = {"schemaVersion": 1, "operations": [{"type": "PROTECT", "ownerPassword": "encrypted"}]}
    request = _request(JobType.PDF_EDIT, Format.PDF, [("acc/doc-1/file", Format.PDF)], {"editPlan": plan})

    service.process(
        ProcessItemCommand(item_id=_queue_request(database, storage, clock, request, [make_text_pdf("a.pdf", ["x"])]))
    )

    failed = database.events[-1]
    assert isinstance(failed, WorkFailed)
    assert (failed.code, failed.retryable) == ("CNV-422-PLAN-001", False)


def test_exports_results_as_one_csv_when_the_job_is_an_export(service, database, storage, clock, tmp_path) -> None:
    results = []
    for number, value in enumerate(("XXXXX1234F", "XXXXX9876G"), start=1):
        path = tmp_path / f"result-{number}.json"
        path.write_text(
            json.dumps({"schemaVersion": 1, "fields": [{"key": "pan", "displayValue": value, "masked": True}]}),
            encoding="utf-8",
        )
        results.append(path)
    request = _request(
        JobType.EXPORT,
        Format.CSV,
        [("acc/doc-1/item/result.json", Format.JSON), ("acc/doc-2/item/result.json", Format.JSON)],
        {},
    )

    service.process(ProcessItemCommand(item_id=_queue_request(database, storage, clock, request, results)))

    completed = _outputs(database)
    (output,) = completed.outputs
    assert (output.ref.key, output.content_type, completed.usage_pages) == ("acc/job/item/results.csv", "text/csv", 2)
    rows = (storage.root / output.ref.bucket / output.ref.key).read_text(encoding="utf-8-sig").splitlines()
    assert rows == ["document,documentType,pan", "doc-1,,XXXXX1234F", "doc-2,,XXXXX9876G"]


def _result_file(tmp_path, name, image_key=None):
    path = tmp_path / name
    pages = [{"index": 0, "imageKey": image_key}] if image_key else []
    path.write_text(
        json.dumps({"schemaVersion": 1, "documentType": {"code": "PAN_CARD"}, "pages": pages, "fields": []}),
        encoding="utf-8",
    )
    return path


@needs_libreoffice
def test_exports_a_pdf_report_with_the_page_image_when_the_export_is_a_pdf(
    service, database, storage, clock, tmp_path, make_image
) -> None:
    storage.put(
        ObjectRef(bucket="idp-derived", key="acc/doc-1/item/page-0.png"), make_image("page.png", size=(120, 80))
    )
    files = [
        _result_file(tmp_path, "r1.json", "acc/doc-1/item/page-0.png"),
        _result_file(tmp_path, "r2.json", "gone.png"),
    ]
    request = _request(
        JobType.EXPORT,
        Format.PDF,
        [("acc/doc-1/item/result.json", Format.JSON), ("acc/doc-2/item/result.json", Format.JSON)],
        {},
    )

    service.process(ProcessItemCommand(item_id=_queue_request(database, storage, clock, request, files)))

    (output,) = _outputs(database).outputs  # the missing second image is left out, not a failure
    assert (output.ref.key, output.content_type) == ("acc/job/item/results.pdf", "application/pdf")
    assert (storage.root / output.ref.bucket / output.ref.key).read_bytes().startswith(b"%PDF")


def test_fails_with_cnv_422_request_001_when_an_export_input_is_not_a_result(
    service, database, storage, clock, tmp_path
) -> None:
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    request = _request(JobType.EXPORT, Format.CSV, [("acc/doc-1/item/result.json", Format.JSON)], {})

    service.process(ProcessItemCommand(item_id=_queue_request(database, storage, clock, request, [broken])))

    failed = database.events[-1]
    assert isinstance(failed, WorkFailed)
    assert failed.code == "CNV-422-REQUEST-001"


def test_fails_with_cnv_422_plan_001_when_the_plan_takes_pages_from_a_document_not_sent(
    service, database, storage, clock, make_text_pdf
) -> None:
    plan = {"schemaVersion": 1, "pages": [{"source": {"documentId": "missing", "page": 1}}]}
    request = _request(JobType.PDF_EDIT, Format.PDF, [("acc/doc-1/file", Format.PDF)], {"editPlan": plan})

    service.process(
        ProcessItemCommand(item_id=_queue_request(database, storage, clock, request, [make_text_pdf("a.pdf", ["x"])]))
    )

    failed = database.events[-1]
    assert isinstance(failed, WorkFailed)
    assert (failed.code, "missing" in failed.detail) == ("CNV-422-PLAN-001", True)


def test_fails_with_cnv_422_limit_001_when_an_inserted_image_is_too_big(
    units, storage, inspector, clock, database, make_text_pdf, tmp_path
) -> None:
    settings = Settings(_env_file=None, edit={"max_image_bytes": 100})
    small_limit = ProcessConversionService(
        units,
        storage,
        inspector,
        build_job_runner(settings, storage),
        clock,
        buckets=OutputBuckets(outputs="idp-outputs", derived="idp-derived"),
        stale_after=STALE_AFTER,
    )
    big = tmp_path / "big.png"
    big.write_bytes(b"x" * 500)
    storage.put(ObjectRef(bucket="idp-derived", key="acc/sig.png"), big)
    plan = {
        "schemaVersion": 1,
        "operations": [
            {"type": "SIGNATURE", "page": 0, "box": [0, 0, 0.5, 0.5], "imageKey": "idp-derived/acc/sig.png"}
        ],
    }
    request = _request(JobType.PDF_EDIT, Format.PDF, [("acc/doc-1/file", Format.PDF)], {"editPlan": plan})

    small_limit.process(
        ProcessItemCommand(item_id=_queue_request(database, storage, clock, request, [make_text_pdf("a.pdf", ["x"])]))
    )

    failed = database.events[-1]
    assert isinstance(failed, WorkFailed)
    assert failed.code == "CNV-422-LIMIT-001"
