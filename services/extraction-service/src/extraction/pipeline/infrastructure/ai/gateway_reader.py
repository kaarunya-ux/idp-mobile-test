"""The AI ports on ai-gateway (IDP-03 ch. 4.2): ``field-read`` (batch form, ai-gateway ADR 0002), ``classify`` and
``page-analysis``.

Pages are already in the derived bucket (the worker stores them before any AI step), so only their keys are sent.
The gateway owns the prompts, the model and the queue; it answers with the values and the model and prompt version
that produced them. Every failure (busy, model down, cap used, a timeout) is ``AiUnavailableError``: fields stay
for review, an unknown document stays unknown, and only deep analysis (whose whole job is the AI) fails the item.
"""

from collections.abc import Mapping, Sequence
from typing import Any

import httpx
import structlog

from extraction.core.service_auth import ServiceTokenError, ServiceTokenProvider
from extraction.pipeline.domain.exceptions import AiUnavailableError
from extraction.pipeline.domain.jobs import ObjectRef
from extraction.pipeline.domain.ports import AiAnalysis, AiCallContext, AiClassification, AiReading
from idp_ocr_engines.model import PageImage

log = structlog.get_logger(__name__)

FIELD_READ = "/internal/v1/ai/field-read"
CLASSIFY = "/internal/v1/ai/classify"
PAGE_ANALYSIS = "/internal/v1/ai/page-analysis"


class AiGatewayClient:
    """One HTTP client and token for every gateway call. Sync: it runs in the Celery workers."""

    def __init__(self, client: httpx.Client, tokens: ServiceTokenProvider) -> None:
        self._client = client  # base URL and timeouts are set by bootstrap
        self._tokens = tokens

    def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            headers = {"Authorization": f"Bearer {self._tokens.token()}"}
            response = self._client.post(path, json=body, headers=headers)
        except (httpx.HTTPError, ServiceTokenError) as exc:
            log.warning("ai_gateway_unreachable", path=path, error=type(exc).__name__)
            raise AiUnavailableError("ai-gateway did not answer") from exc
        if response.status_code == httpx.codes.UNAUTHORIZED:
            self._tokens.invalidate()  # the next call fetches a new token
        if response.status_code != httpx.codes.OK:
            log.warning("ai_gateway_error", path=path, status=response.status_code, code=_code(response))
            raise AiUnavailableError(f"ai-gateway answered {response.status_code}")
        try:
            answer = response.json()
        except ValueError as exc:
            raise AiUnavailableError("ai-gateway's answer could not be read") from exc
        if not isinstance(answer, dict):
            raise AiUnavailableError("ai-gateway's answer could not be read")
        return answer


def caller(context: AiCallContext) -> dict[str, str]:
    return {"accountId": str(context.account_id), "priority": "PRIORITY" if context.priority else "STANDARD"}


def ref(found: ObjectRef) -> dict[str, str]:
    return {"bucket": found.bucket, "key": found.key}


class GatewayFieldReader:
    def __init__(self, gateway: AiGatewayClient) -> None:
        self._gateway = gateway

    def read_fields(
        self, document_type: str, pages: Sequence[PageImage], keys: Sequence[str], context: AiCallContext
    ) -> AiReading:
        body = caller(context) | {
            "documentType": document_type,
            "pages": [ref(page) for page in context.page_refs],
            "fields": [
                {"key": key, **({"label": context.labels[key]} if key in context.labels else {})} for key in keys
            ],
        }
        answer = self._gateway.post(FIELD_READ, body)
        try:
            values = {str(f["key"]): _text(f.get("value")) for f in answer["fields"]}
            return AiReading(
                values={key: values.get(key) for key in keys},
                model_id=str(answer["model"]),
                prompt_version=str(answer["promptVersion"]),
            )
        except (KeyError, TypeError) as exc:
            raise AiUnavailableError("ai-gateway's answer could not be read") from exc


class GatewayClassifier:
    def __init__(self, gateway: AiGatewayClient) -> None:
        self._gateway = gateway

    def classify(self, page: ObjectRef, candidates: Sequence[str], context: AiCallContext) -> AiClassification:
        answer = self._gateway.post(CLASSIFY, caller(context) | {"page": ref(page), "candidates": list(candidates)})
        try:
            found = answer.get("documentType")
            return AiClassification(
                document_type=str(found) if found in candidates else None,
                confidence=float(answer["confidence"]),
                model_id=str(answer["model"]),
                prompt_version=str(answer["promptVersion"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise AiUnavailableError("ai-gateway's answer could not be read") from exc


class GatewayPageAnalyser:
    def __init__(self, gateway: AiGatewayClient) -> None:
        self._gateway = gateway

    def analyse(
        self, pages: Sequence[ObjectRef], current: Mapping[str, object], question: str | None, context: AiCallContext
    ) -> AiAnalysis:
        body = caller(context) | {"pages": [ref(p) for p in pages], "currentResult": dict(current)}
        if question:
            body["question"] = question
        answer = self._gateway.post(PAGE_ANALYSIS, body)
        try:
            return AiAnalysis(
                fields=[dict(f) for f in answer["fields"]],
                summary=str(answer["summary"]),
                warnings=[str(w) for w in answer.get("warnings", [])],
                model_id=str(answer["model"]),
                prompt_version=str(answer["promptVersion"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise AiUnavailableError("ai-gateway's answer could not be read") from exc


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _code(response: httpx.Response) -> str | None:
    """The problem-details code, for the log; never the detail text."""
    try:
        code = response.json().get("code")
    except (ValueError, AttributeError):
        return None
    return code if isinstance(code, str) else None
