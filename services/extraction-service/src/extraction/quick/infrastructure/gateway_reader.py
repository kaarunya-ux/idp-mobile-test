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
from extraction.quick.domain.lines import LinesField, LinesReading
from extraction.quick.domain.models import ExtractedField, QuickCaller, QuickResult, ReadBy, StagedFile, Table

if TYPE_CHECKING:  # the type only: the store imports Pillow, which the slim API image has not
    from extraction.pipeline.infrastructure.ai.inline_images import InlineImages

log = structlog.get_logger(__name__)

GENERIC_READ = "/internal/v1/ai/generic-read"
GENERIC_LINES = "/internal/v1/ai/generic-lines"
_BUSY = "AIG-503-BUSY-001"
_CAP = "AIG-429-CAP-001"
_INPUT = "AIG-422-INPUT-001"


class GatewayDocumentReader:
    def __init__(self, client: httpx.Client, tokens: ServiceTokenProvider, images: "InlineImages") -> None:
        self._client = client  # base URL and timeout are set by bootstrap
        self._tokens = tokens
        self._images = images  # the request's pages, sent inside the call (ADR 0012)

    def read(self, caller: QuickCaller, pages: Sequence[StagedFile]) -> QuickResult:
        answer = self._post(GENERIC_READ, caller, {"pages": [self._image(p) for p in pages]})
        try:
            return _result(answer)
        except (ValueError, KeyError, TypeError, AttributeError) as exc:  # not the documented shape
            raise AiUnavailableError from exc

    def read_lines(self, caller: QuickCaller, lines: Sequence[str]) -> LinesReading:
        answer = self._post(GENERIC_LINES, caller, {"lines": [{"n": n, "text": t} for n, t in enumerate(lines)]})
        try:
            return _lines_reading(answer)
        except (ValueError, KeyError, TypeError, AttributeError) as exc:  # not the documented shape
            raise AiUnavailableError from exc

    def _post(self, path: str, caller: QuickCaller, payload: Mapping[str, Any]) -> Any:
        body = {"accountId": str(caller.account_id), "priority": "INTERACTIVE", "plan": caller.plan, **payload}
        try:
            headers = {"Authorization": f"Bearer {self._tokens.token()}"}
            response = self._client.post(path, json=body, headers=headers)
        except (httpx.HTTPError, ServiceTokenError) as exc:
            log.warning("ai_gateway_unreachable", path=path, error=type(exc).__name__)
            raise AiUnavailableError from exc
        if response.status_code != httpx.codes.OK:
            raise self._failure(response, path)
        try:
            return response.json()
        except ValueError as exc:
            raise AiUnavailableError from exc

    def _image(self, page: StagedFile) -> dict[str, str]:
        data = self._images.get(ObjectRef(bucket=page.bucket, key=page.key))
        if data is None:
            return {"bucket": page.bucket, "key": page.key}
        return {"data": base64.b64encode(data).decode()}

    def _failure(self, response: httpx.Response, path: str) -> DomainError:
        code = _code(response)
        log.warning("ai_gateway_error", path=path, status=response.status_code, code=code)
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


def _lines_reading(answer: Mapping[str, Any]) -> LinesReading:
    confidence = answer.get("documentConfidence")
    return LinesReading(
        document_type=str(answer["documentType"]) if answer.get("documentType") else None,
        document_confidence=float(confidence) if confidence is not None else None,
        fields=[
            LinesField(
                key=str(f["key"]),
                label=str(f["label"]),
                type=str(f["type"]),
                lines=tuple(int(n) for n in f.get("lines") or []),
                rows=tuple(tuple(tuple(int(n) for n in cell) for cell in row) for row in f["rows"])
                if f.get("rows") is not None
                else None,
            )
            for f in answer["fields"]
        ],
        model_id=str(answer["model"]),
        prompt_version=str(answer["promptVersion"]),
    )


def _code(response: httpx.Response) -> str | None:
    """The problem-details code, for the log and the mapping; never the detail text."""
    try:
        code = response.json().get("code")
    except (ValueError, AttributeError):
        return None
    return code if isinstance(code, str) else None
