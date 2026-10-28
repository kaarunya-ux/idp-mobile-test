"""The Prometheus adapter, and the designer's API-side adapters (Celery, MinIO, ai-gateway) on fakes."""

import json
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest

from extraction.pipeline.domain.jobs import JobKind
from extraction.pipeline.domain.runs import PipelineRun
from extraction.pipeline.infrastructure.ai.gateway_reader import AiGatewayClient
from extraction.pipeline.infrastructure.metrics import (
    FIELD_DECISIONS,
    ITEMS_FAILED,
    QUALITY_REJECTIONS,
    PrometheusPipelineMetrics,
)
from extraction.template.domain.designer import SampleFile
from extraction.template.domain.exceptions import SuggestionsUnavailableError
from extraction.template.infrastructure.designer_adapters import (
    PREPARE_SAMPLE_TASK,
    TEST_RUN_TASK,
    CeleryDesignerTasks,
    GatewayTemplateSuggester,
    S3SampleStorage,
)


def test_counts_decisions_failures_and_quality_rejections() -> None:
    run = PipelineRun(
        item_id=uuid4(),
        account_id=uuid4(),
        kind=JobKind.EXTRACTION,
        document_type="PAN_CARD",
        template_id=None,
        template_version=None,
        stages={"READ": 1200},
        decisions={"OCR_AGREE/ACCEPTED": 3},
        ai_calls=1,
        duration_ms=1500,
        created_at=datetime(2026, 10, 28, tzinfo=UTC),
    )
    before = FIELD_DECISIONS.labels(source="OCR_AGREE", status="ACCEPTED", document_type="PAN_CARD")._value.get()
    metrics = PrometheusPipelineMetrics()

    metrics.run_finished(run)
    metrics.item_failed("EXT-422-QUALITY-001")

    assert (
        FIELD_DECISIONS.labels(source="OCR_AGREE", status="ACCEPTED", document_type="PAN_CARD")._value.get()
        == before + 3
    )
    assert QUALITY_REJECTIONS.labels(reason="EXT-422-QUALITY-001")._value.get() >= 1
    assert ITEMS_FAILED.labels(code="EXT-422-QUALITY-001")._value.get() >= 1


class Celery:
    def __init__(self) -> None:
        self.sent: list[tuple[str, list[str], str]] = []

    def send_task(self, name: str, *, args: list[str], queue: str) -> None:
        self.sent.append((name, args, queue))


async def test_sends_sample_and_test_run_tasks_to_the_pipeline_queue() -> None:
    celery, sample, run = Celery(), uuid4(), uuid4()
    tasks = CeleryDesignerTasks(celery)

    await tasks.prepare_sample(sample)
    await tasks.run_test(run)

    assert celery.sent == [(PREPARE_SAMPLE_TASK, [str(sample)], "pipeline"), (TEST_RUN_TASK, [str(run)], "pipeline")]


class _Body:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


class S3:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    def get_object(self, *, Bucket: str, Key: str) -> dict:  # noqa: N803
        return {"Body": _Body(json.dumps([{"text": "Name"}]).encode())}

    def get_paginator(self, name: str):
        class Pages:
            def paginate(self, *, Bucket: str, Prefix: str):  # noqa: N803
                return [{"Contents": [{"Key": f"{Prefix}a"}, {"Key": f"{Prefix}b"}]}, {}]

        return Pages()

    def delete_objects(self, *, Bucket: str, Delete: dict) -> None:  # noqa: N803
        self.deleted += [o["Key"] for o in Delete["Objects"]]


async def test_reads_sample_words_and_deletes_an_accounts_samples() -> None:
    s3, account = S3(), uuid4()
    storage = S3SampleStorage(s3, bucket="idp-templates")

    assert await storage.read_words("k/words.json") == [{"text": "Name"}]
    assert await storage.delete_account(account) == 2
    assert s3.deleted == [f"{account}/a", f"{account}/b"]


class Tokens:
    def token(self) -> str:
        return "t"

    def invalidate(self) -> None:
        return None


def _gateway(answer: httpx.Response) -> AiGatewayClient:
    client = httpx.Client(base_url="http://ai-gateway:8002", transport=httpx.MockTransport(lambda _: answer))
    return AiGatewayClient(client, Tokens())


async def test_returns_suggested_fields_from_the_gateway() -> None:
    reply = {"fields": [{"key": "name"}], "tables": [], "model": "vl", "promptVersion": "template-suggest.v1"}
    suggester = GatewayTemplateSuggester(_gateway(httpx.Response(200, json=reply)))

    found = await suggester.suggest(SampleFile(bucket="idp-templates", key="k"), [{"text": "Name"}], uuid4())

    assert (found.fields, found.prompt_version) == ([{"key": "name"}], "template-suggest.v1")


@pytest.mark.parametrize("gateway", [None, "down"])
async def test_answers_ai_unavailable_when_suggestions_cannot_be_had(gateway) -> None:
    client = _gateway(httpx.Response(503)) if gateway else None

    with pytest.raises(SuggestionsUnavailableError):
        await GatewayTemplateSuggester(client).suggest(SampleFile(bucket="idp-templates", key="k"), [], uuid4())
