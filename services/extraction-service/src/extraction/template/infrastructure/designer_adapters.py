"""The designer's adapters for the API process: Celery tasks, the prepared samples in MinIO, and ai-gateway's
``template-suggest``. Celery, boto3 and the gateway client are blocking, so each call runs in a thread (ASY-01)."""

import asyncio
import json
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any
from uuid import UUID

import structlog
from celery import Celery

from extraction.pipeline.domain.exceptions import AiUnavailableError
from extraction.pipeline.infrastructure.ai.gateway_reader import AiGatewayClient
from extraction.template.domain.designer import SampleFile
from extraction.template.domain.exceptions import SuggestionsUnavailableError
from extraction.template.domain.ports import Suggestion

if TYPE_CHECKING:  # boto3-stubs is a dev dependency: type hints only, not in the images
    from mypy_boto3_s3 import S3Client

log = structlog.get_logger(__name__)

PREPARE_SAMPLE_TASK = "extraction.prepare_sample"
TEST_RUN_TASK = "extraction.run_template_test"
PIPELINE_QUEUE = "pipeline"
TEMPLATE_SUGGEST = "/internal/v1/ai/template-suggest"
_MAX_WORDS = 3000  # ai-gateway's limit


class CeleryDesignerTasks:
    def __init__(self, app: Celery) -> None:
        self._app = app

    async def prepare_sample(self, sample_id: UUID) -> None:
        await asyncio.to_thread(self._app.send_task, PREPARE_SAMPLE_TASK, args=[str(sample_id)], queue=PIPELINE_QUEUE)

    async def run_test(self, run_id: UUID) -> None:
        await asyncio.to_thread(self._app.send_task, TEST_RUN_TASK, args=[str(run_id)], queue=PIPELINE_QUEUE)


class S3SampleStorage:
    def __init__(self, client: "S3Client", *, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    async def read_words(self, key: str) -> list[dict[str, Any]]:
        def read() -> list[dict[str, Any]]:
            data = self._client.get_object(Bucket=self._bucket, Key=key)["Body"].read()
            words = json.loads(data)
            return list(words) if isinstance(words, list) else []

        return await asyncio.to_thread(read)

    async def delete_account(self, account_id: UUID) -> int:
        def delete() -> int:
            deleted = 0
            paginator = self._client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self._bucket, Prefix=f"{account_id}/"):
                keys = [{"Key": item["Key"]} for item in page.get("Contents", [])]
                if keys:
                    self._client.delete_objects(Bucket=self._bucket, Delete={"Objects": keys, "Quiet": True})  # type: ignore[typeddict-item]  # boto3-stubs wants ObjectIdentifierTypeDef; these dicts are that shape
                    deleted += len(keys)
            return deleted

        return await asyncio.to_thread(delete)


class GatewayTemplateSuggester:
    def __init__(self, gateway: AiGatewayClient | None) -> None:
        self._gateway = gateway

    async def suggest(self, page: SampleFile, words: Sequence[dict[str, Any]], account_id: UUID | None) -> Suggestion:
        if self._gateway is None or account_id is None:  # standard templates are staff work: no account to cap
            raise SuggestionsUnavailableError
        body = {
            "accountId": str(account_id),
            "page": {"bucket": page.bucket, "key": page.key},
            "ocrWords": list(words)[:_MAX_WORDS],
        }
        try:
            answer = await asyncio.to_thread(self._gateway.post, TEMPLATE_SUGGEST, body)
            return Suggestion(
                fields=[dict(f) for f in answer["fields"]],
                tables=[dict(t) for t in answer.get("tables", [])],
                model_id=str(answer["model"]),
                prompt_version=str(answer["promptVersion"]),
            )
        except (AiUnavailableError, KeyError, TypeError) as exc:
            log.warning("template_suggest_unavailable", error=type(exc).__name__)
            raise SuggestionsUnavailableError from exc
