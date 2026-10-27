"""Job items, their requests, object keys, job options and pools: the rules step 4 runs on."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from conversion.conversion.domain.events import ItemContext
from conversion.conversion.domain.exceptions import InvalidOptionError, InvalidRequestError
from conversion.conversion.domain.formats import Format
from conversion.conversion.domain.models import ConversionItem, ConversionRequest, ItemStatus
from conversion.conversion.domain.options import parse_job_options, resolve_target
from conversion.conversion.domain.policies import worker_pool_for
from conversion.conversion.domain.route_planner import OcrMode, RoutePlanner, RouteRequest
from conversion.conversion.domain.values import InputFile, JobType, ObjectRef, OutputMode, WorkerPool

NOW = datetime(2026, 10, 26, 9, 0, tzinfo=UTC)
CONTEXT = ItemContext(tenant_id=uuid4(), correlation_id="c", job_id=uuid4(), item_id=uuid4())


def _request(*formats: Format, prefix: str = "acc/job/item") -> ConversionRequest:
    inputs = tuple(
        InputFile(ref=ObjectRef(bucket="idp-originals", key=f"k/{n}"), format=f) for n, f in enumerate(formats)
    )
    return ConversionRequest(context=CONTEXT, inputs=inputs, target=Format.PDF, options={}, output_prefix=prefix)


@pytest.mark.parametrize(
    ("bucket", "key"),
    [
        ("Bad_Bucket", "a"),
        ("ok-bucket", ""),
        ("ok-bucket", "/abs"),
        ("ok-bucket", "a/../b"),
        ("ok-bucket", "a\\b"),
        ("ok-bucket", "a\nb"),
    ],
)
def test_refuses_an_object_ref_when_the_bucket_or_key_is_unsafe(bucket: str, key: str) -> None:
    with pytest.raises(InvalidRequestError):
        ObjectRef(bucket=bucket, key=key)


@pytest.mark.parametrize(
    ("formats", "prefix", "message"),
    [
        ((), "acc/job", "1 to 200"),
        ((Format.PDF, Format.PDF), "acc/job", "Only images"),
        ((Format.PNG,), "../escape", "output prefix"),
        ((Format.PNG,), "/", "output prefix"),
    ],
)
def test_refuses_a_request_when_its_inputs_or_prefix_cannot_be_used(formats, prefix: str, message: str) -> None:
    with pytest.raises(InvalidRequestError, match=message):
        _request(*formats, prefix=prefix)


@pytest.mark.parametrize(
    ("job_type", "formats", "target", "message"),
    [
        (JobType.PDF_MERGE, (Format.PDF, Format.DOCX), Format.PDF, "Only PDFs and images"),
        (JobType.PDF_MERGE, (Format.PDF, Format.PDF), Format.DOCX, "always makes a PDF"),
        (JobType.PREVIEW, (Format.PDF, Format.PDF), Format.JPEG, "one document at a time"),
        (JobType.PREVIEW, (Format.PDF,), Format.PDF, "Previews are images"),
        (JobType.PDF_EDIT, (Format.PDF, Format.PNG), Format.PDF, "An edit takes PDFs"),
        (JobType.EXPORT, (Format.PDF,), Format.CSV, "extraction results"),
        (JobType.EXPORT, (Format.JSON,), Format.PNG, "can't be exported as PNG"),
    ],
)
def test_refuses_a_merge_or_preview_when_its_inputs_or_target_do_not_fit_the_job_type(
    job_type: JobType, formats: tuple[Format, ...], target: Format, message: str
) -> None:
    inputs = tuple(
        InputFile(ref=ObjectRef(bucket="idp-originals", key=f"k/{n}"), format=f) for n, f in enumerate(formats)
    )

    with pytest.raises(InvalidRequestError, match=message):
        ConversionRequest(
            context=CONTEXT, inputs=inputs, target=target, options={}, output_prefix="acc/job", job_type=job_type
        )


def test_accepts_pdfs_and_images_together_when_the_job_is_a_merge() -> None:
    inputs = tuple(
        InputFile(ref=ObjectRef(bucket="idp-originals", key=f"k/{n}"), format=f)
        for n, f in enumerate((Format.PDF, Format.JPEG, Format.PDF))
    )

    request = ConversionRequest(
        context=CONTEXT, inputs=inputs, target=Format.PDF, options={}, output_prefix="a/b", job_type=JobType.PDF_MERGE
    )

    assert len(request.inputs) == 3


def test_builds_output_keys_under_the_prefix_without_doubled_slashes() -> None:
    request = _request(Format.PNG, Format.JPEG, prefix="/acc/job/item/")

    assert request.output_key("document.pdf") == "acc/job/item/document.pdf"
    assert request.source is Format.PNG


def test_counts_attempts_and_sequences_events_through_the_lifecycle() -> None:
    item = ConversionItem.receive(_request(Format.PNG), NOW)

    started = item.start(NOW)
    progressed = item.progress(item_stage := started.stage, 50, NOW + timedelta(seconds=1))
    completed = item.complete((), 2, NOW + timedelta(seconds=2))

    assert (started.sequence, progressed.sequence, completed.sequence) == (1, 2, 3)
    assert item_stage == "PREPARE"
    assert (item.status, item.attempts, item.stored_completion()) == (ItemStatus.COMPLETED, 1, completed)


def test_requeues_only_a_running_item_when_a_dependency_fails() -> None:
    item = ConversionItem.receive(_request(Format.PNG), NOW)
    item.requeue(NOW)
    assert item.status is ItemStatus.QUEUED
    item.start(NOW)

    item.requeue(NOW)

    assert item.status is ItemStatus.QUEUED


def test_raises_when_a_completed_item_has_no_stored_result() -> None:
    item = ConversionItem.receive(_request(Format.PNG), NOW)
    item.status = ItemStatus.COMPLETED

    with pytest.raises(ValueError, match="without a stored result"):
        item.stored_completion()


def test_reads_every_known_job_option_and_ignores_unknown_ones() -> None:
    options = parse_job_options(
        {"pageRange": "2-3", "dpi": 300, "quality": 80, "sheet": "Q3", "ocr": "on", "output": "zip", "colour": "x"}
    )

    assert options.conversion.page_range is not None
    assert options.conversion.page_range.select(5) == [1, 2]
    assert (options.conversion.dpi, options.conversion.jpeg_quality, options.conversion.sheet) == (300, 80, "Q3")
    assert (options.ocr, options.output) == (OcrMode.ON, OutputMode.ZIP)


def test_uses_defaults_when_no_options_are_given() -> None:
    options = parse_job_options({})

    assert (options.conversion.dpi, options.ocr, options.output) == (150, OcrMode.AUTO, OutputMode.PER_PAGE)


@pytest.mark.parametrize(
    "raw",
    [{"dpi": "300"}, {"dpi": True}, {"pageRange": 3}, {"output": "FAX"}, {"ocr": "maybe"}],
)
def test_reports_cnv_422_options_001_when_an_option_has_the_wrong_type_or_value(raw: dict[str, object]) -> None:
    with pytest.raises(InvalidOptionError) as caught:
        parse_job_options(raw)

    assert caught.value.code == "CNV-422-OPTIONS-001"


@pytest.mark.parametrize(
    ("target", "image_format", "expected"),
    [
        ("docx", None, Format.DOCX),
        ("IMAGE", None, Format.PNG),
        ("IMAGE", "jpeg", Format.JPEG),
        ("JPG", None, Format.JPEG),
    ],
)
def test_resolves_the_target_format_including_image_with_its_format(target, image_format, expected) -> None:
    assert resolve_target(target, image_format) is expected


def test_reports_an_invalid_target_when_the_format_is_unknown() -> None:
    with pytest.raises(InvalidOptionError, match="target"):
        resolve_target("ODT")


@pytest.mark.parametrize(
    ("content_type", "expected"),
    [
        ("application/pdf", Format.PDF),
        ("text/csv; charset=utf-8", Format.CSV),
        ("IMAGE/JPG", Format.JPEG),
        ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", Format.DOCX),
        ("text/html", None),
    ],
)
def test_reads_the_input_format_from_the_detected_content_type(content_type: str, expected: Format | None) -> None:
    assert Format.from_content_type(content_type) is expected


@pytest.mark.parametrize(
    ("source", "target", "ocr", "pool"),
    [
        (Format.PDF, Format.PNG, OcrMode.AUTO, WorkerPool.PDF),
        (Format.XLSX, Format.PDF, OcrMode.AUTO, WorkerPool.OFFICE),
        (Format.PPTX, Format.CSV, OcrMode.AUTO, WorkerPool.DATA),
        (Format.PNG, Format.PDF, OcrMode.ON, WorkerPool.OCR),
    ],
)
def test_picks_the_pool_of_the_scarcest_resource_a_route_needs(source, target, ocr, pool) -> None:
    route = RoutePlanner().get_route(RouteRequest(source=source, target=target, ocr=ocr))

    assert worker_pool_for(route) is pool
