"""Rows <-> items: an item and its stored result survive the trip through the database columns unchanged."""

from dataclasses import replace

from extraction.pipeline.domain.jobs import ExtractionItem, ExtractionOutcome, ObjectRef, ResultSummary
from extraction.pipeline.infrastructure.persistence.mappers import to_domain, to_orm
from support.job_fakes import START, request


def test_reads_back_an_item_with_its_request_and_result_unchanged() -> None:
    item = ExtractionItem.receive(request(files=2), START)
    item.start(START)
    item.complete(
        ExtractionOutcome(
            result=ObjectRef(bucket="idp-derived", key="a/d/i/result.json"),
            document_type="AADHAAR_CARD",
            template_id="standard-aadhaar-card",
            template_version=1,
            summary=ResultSummary(fields=8, needs_review=2, avg_confidence=0.88),
            pages=2,
            ai_calls=1,
        ),
        START,
    )

    again = to_domain(to_orm(item))

    assert again.request == item.request
    assert again.completion == item.completion
    assert (again.status, again.attempts, again.sequence) == (item.status, 1, 2)


def test_keeps_the_priority_route_and_reads_older_rows_as_standard() -> None:
    item = ExtractionItem.receive(replace(request(), priority=True), START)
    row = to_orm(item)

    assert to_domain(row).request.priority
    row.request = {key: value for key, value in row.request.items() if key != "priority"}  # a row from before
    assert not to_domain(row).request.priority
