"""``DocumentReaderPort`` on ai-gateway's ``generic-read``, on the INTERACTIVE lane (ai-gateway ADR 0003).

For a document no template matched. Synchronous, like the use case, which runs in a worker thread of the sync
process. The gateway's codes become this service's: busy and the daily cap are told apart, so the backend knows
whether sending the document the queued way would help. Only codes and statuses are logged, never document content.
"""

import base64
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

import httpx
import structlog

from extraction.core.errors import DomainError
from extraction.core.service_auth import ServiceTokenError, ServiceTokenProvider
from extraction.pipeline.domain.jobs import ObjectRef
from extraction.quick.domain.exceptions import (
    AiLimitReachedError,
    AiUnavailableError,
    BusyError,
    FileUnreadableError,
)
from extraction.quick.domain.models import ExtractedField, QuickCaller, QuickResult, ReadBy, StagedFile, Table

if TYPE_CHECKING:  # the type only: the store imports Pillow, which the slim API image has not
    from extraction.pipeline.infrastructure.ai.inline_images import InlineImages

log = structlog.get_logger(__name__)

GENERIC_READ = "/internal/v1/ai/generic-read"
_BUSY = "AIG-503-BUSY-001"
_CAP = "AIG-429-CAP-001"
_INPUT = "AIG-422-INPUT-001"


class GatewayDocumentReader:
    def __init__(self, client: httpx.Client, tokens: ServiceTokenProvider, images: "InlineImages") -> None:
        self._client = client  # base URL and timeout are set by bootstrap
        self._tokens = tokens
        self._images = images  # the request's pages, sent inside the call (ADR 0012)

    def read(self, caller: QuickCaller, pages: Sequence[StagedFile]) -> QuickResult:
        body = {
            "accountId": str(caller.account_id),
            "priority": "INTERACTIVE",
            "plan": caller.plan,
            "pages": [self._image(p) for p in pages],
        }
        try:
            headers = {"Authorization": f"Bearer {self._tokens.token()}"}
            response = self._client.post(GENERIC_READ, json=body, headers=headers)
        except (httpx.HTTPError, ServiceTokenError) as exc:
            log.warning("ai_gateway_unreachable", path=GENERIC_READ, error=type(exc).__name__)
            raise AiUnavailableError from exc
        if response.status_code != httpx.codes.OK:
            raise self._failure(response)
        try:
            return _result(response.json())
        except (ValueError, KeyError, TypeError, AttributeError) as exc:  # not the documented shape
            raise AiUnavailableError from exc

    def _image(self, page: StagedFile) -> dict[str, str]:
        data = self._images.get(ObjectRef(bucket=page.bucket, key=page.key))
        if data is None:
            return {"bucket": page.bucket, "key": page.key}
        return {"data": base64.b64encode(data).decode()}

    def _failure(self, response: httpx.Response) -> DomainError:
        code = _code(response)
        log.warning("ai_gateway_error", path=GENERIC_READ, status=response.status_code, code=code)
        if response.status_code == httpx.codes.UNAUTHORIZED:
            self._tokens.invalidate()  # the next call fetches a new token
        if code == _BUSY:
            return BusyError()
        if code == _CAP:
            return AiLimitReachedError()
        if code == _INPUT:
            return FileUnreadableError("The file could not be read as a document. Send a clear PDF, JPG, PNG or WebP.")
        return AiUnavailableError()


def _result(answer: Mapping[str, Any]) -> QuickResult:
    found = answer.get("documentType")
    confidence = answer.get("documentConfidence")
    return QuickResult(
        document_type=str(found) if found else None,
        document_confidence=float(confidence) if confidence is not None else None,
        fields=[_field(f) for f in answer["fields"]],
        read_by=ReadBy.AI,
        model_id=str(answer["model"]),
        prompt_version=str(answer["promptVersion"]),
    )


def _field(found: Mapping[str, Any]) -> ExtractedField:
    raw, confidence = found.get("value"), found.get("confidence")
    value: str | Table | None
    if isinstance(raw, Mapping):
        value = Table(headers=[str(h) for h in raw["headers"]], rows=[[str(c) for c in row] for row in raw["rows"]])
    else:
        value = str(raw) if raw is not None else None
    return ExtractedField(
        key=str(found["key"]),
        label=str(found["label"]),
        type=str(found["type"]),
        value=value,
        confidence=float(confidence) if confidence is not None else None,
    )


def _code(response: httpx.Response) -> str | None:
    """The problem-details code, for the log and the mapping; never the detail text."""
    try:
        code = response.json().get("code")
    except (ValueError, AttributeError):
        return None
    return code if isinstance(code, str) else None
