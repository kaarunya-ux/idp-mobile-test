"""The AI ports on ai-gateway (IDP-03 ch. 4.2): ``field-read`` (its single form on one field's crop, ADR 0009, and
its batch form on the whole page, ai-gateway ADR 0002), ``classify`` and ``page-analysis``.

Pages are already in the derived bucket (the worker stores them before any AI step), so only their keys are sent. In
the synchronous lane they are held in memory (``InlineImages``) and sent inside the request instead (ADR 0012).
The gateway owns the prompts, the model and the queue; it answers with the values and the model and prompt version
that produced them. Every failure (busy, model down, cap used, a timeout) is ``AiUnavailableError``: fields stay
for review, an unknown document stays unknown, and only deep analysis (whose whole job is the AI) fails the item.
"""

import base64
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

import httpx
import structlog

from extraction.core.service_auth import ServiceTokenError, ServiceTokenProvider
from extraction.pipeline.domain.exceptions import AiUnavailableError
from extraction.pipeline.domain.jobs import ObjectRef
from extraction.pipeline.domain.ports import AiAnalysis, AiCallContext, AiClassification, AiFieldCrop, AiReading
from idp_ocr_engines.model import PageImage

if TYPE_CHECKING:  # the type only: the store imports Pillow, which the slim API image has not
    from extraction.pipeline.infrastructure.ai.inline_images import InlineImages

log = structlog.get_logger(__name__)

FIELD_READ = "/internal/v1/ai/field-read"
CLASSIFY = "/internal/v1/ai/classify"
PAGE_ANALYSIS = "/internal/v1/ai/page-analysis"
_SLOTS = frozenset({"A", "B", "C"})  # the engines' readings the gateway takes as hints
_READING_LENGTH = 400  # the gateway's limit for one hint


class AiGatewayClient:
    """One HTTP client and token for every gateway call. Sync: it runs in the Celery workers and the sync process."""

    def __init__(
        self, client: httpx.Client, tokens: ServiceTokenProvider, inline: "InlineImages | None" = None
    ) -> None:
        self._client = client  # base URL and timeouts are set by bootstrap
        self._tokens = tokens
        self._inline = inline

    def image(self, found: ObjectRef) -> dict[str, str]:
        """How the gateway gets this image: its bytes in the request when it is held in memory, else its key."""
        data = self._inline.get(found) if self._inline is not None else None
        return {"data": base64.b64encode(data).decode()} if data is not None else ref(found)

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
    """Whose call it is, and which of ai-gateway's lanes it waits in: INTERACTIVE when a person waits (ADR 0009)."""
    lane = "INTERACTIVE" if context.interactive else "PRIORITY" if context.priority else "STANDARD"
    return {"accountId": str(context.account_id), "priority": lane} | ({"plan": context.plan} if context.plan else {})


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
            "pages": [self._gateway.image(page) for page in context.page_refs],
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


class GatewayCropReader:
    """One field from its crop, with the engines' readings as hints. The model's own confidence is not used: the
    caller gives an AI value its confidence (``AI_GATEWAY__CONFIDENCE``, ML-07), as for the batch form."""

    def __init__(self, gateway: AiGatewayClient) -> None:
        self._gateway = gateway

    def read_crop(self, field: AiFieldCrop, context: AiCallContext) -> AiReading:
        hints = {slot: text[:_READING_LENGTH] for slot, text in field.readings.items() if text and slot in _SLOTS}
        body = caller(context) | {
            "crop": self._gateway.image(field.crop),
            "field": {"key": field.key, "label": field.label, "type": field.type},
            "readings": hints,
        }
        answer = self._gateway.post(FIELD_READ, body)
        try:
            return AiReading(
                values={field.key: _text(answer.get("value"))},
                model_id=str(answer["model"]),
                prompt_version=str(answer["promptVersion"]),
            )
        except (KeyError, TypeError, AttributeError) as exc:
            raise AiUnavailableError("ai-gateway's answer could not be read") from exc


class GatewayClassifier:
    def __init__(self, gateway: AiGatewayClient) -> None:
        self._gateway = gateway

    def classify(self, page: ObjectRef, candidates: Sequence[str], context: AiCallContext) -> AiClassification:
        body = caller(context) | {"page": self._gateway.image(page), "candidates": list(candidates)}
        answer = self._gateway.post(CLASSIFY, body)
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
        body = caller(context) | {"pages": [self._gateway.image(p) for p in pages], "currentResult": dict(current)}
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
