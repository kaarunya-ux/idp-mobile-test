"""Job items and their requests: what a request may hold, and the item's lifecycle."""

from datetime import timedelta
from uuid import uuid4

import pytest

from extraction.pipeline.domain.exceptions import RequestRejectedError
from extraction.pipeline.domain.jobs import ExtractionItem, ExtractionRequest, ItemContext, ItemStatus, ObjectRef
from support.job_fakes import START, request


@pytest.mark.parametrize(
    ("bucket", "key"),
    [("Bad_Bucket", "a"), ("ok-bucket", ""), ("ok-bucket", "/abs"), ("ok-bucket", "a/../b"), ("ok-bucket", "a\\b")],
)
def test_refuses_an_object_ref_when_the_bucket_or_key_is_unsafe(bucket: str, key: str) -> None:
    with pytest.raises(RequestRejectedError):
        ObjectRef(bucket=bucket, key=key)


@pytest.mark.parametrize("count", [0, 3])
def test_refuses_a_request_when_it_has_no_file_or_more_than_two(count: int) -> None:
    context = ItemContext(tenant_id=uuid4(), correlation_id="c", job_id=uuid4(), item_id=uuid4())
    files = tuple(ObjectRef(bucket="idp-originals", key=f"k/{n}") for n in range(count))

    with pytest.raises(RequestRejectedError, match="1 to 2 files"):
        ExtractionRequest(context=context, document_id=uuid4(), files=files, template_id=None, options={})


def test_builds_derived_keys_as_account_document_item() -> None:
    req = request()

    assert (
        req.derived_key("result.json") == f"{req.context.tenant_id}/{req.document_id}/{req.context.item_id}/result.json"
    )


def test_counts_attempts_and_sequences_events_through_the_lifecycle() -> None:
    item = ExtractionItem.receive(request(), START)

    started = item.start(START)
    progressed = item.progress(started.stage, 50, START + timedelta(seconds=1))
    failed = item.fail("EXT-422-QUALITY-001", retryable=False, detail="blurred", now=START)  # type: ignore[arg-type]

    assert (started.sequence, progressed.sequence, failed.sequence) == (1, 2, 3)
    assert (item.status, item.attempts, item.error_code) == (ItemStatus.FAILED, 1, "EXT-422-QUALITY-001")


def test_requeues_only_a_running_item() -> None:
    item = ExtractionItem.receive(request(), START)
    item.requeue(START)
    item.start(START)

    item.requeue(START)

    assert item.status is ItemStatus.QUEUED


def test_raises_when_a_completed_item_has_no_stored_result() -> None:
    item = ExtractionItem.receive(request(), START)
    item.status = ItemStatus.COMPLETED

    with pytest.raises(ValueError, match="without a stored result"):
        item.stored_completion()
