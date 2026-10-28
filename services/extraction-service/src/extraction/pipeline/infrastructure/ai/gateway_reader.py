"""``AiFieldReaderPort`` on ai-gateway's ``field-read`` (IDP-03 ch. 4.2, batch form; ai-gateway ADR 0002).

The pages are already in the derived bucket (the worker stores them before the AI step), so only their keys are
sent. The gateway owns the prompts, the model and the queue; it answers with the values and the model and prompt
version that read them. Every failure (busy, model down, cap used, a timeout) is ``AiUnavailableError``: the fields
then stay for review, and the job never fails because of the AI.
"""

from collections.abc import Sequence

import httpx
import structlog

from extraction.core.service_auth import ServiceTokenError, ServiceTokenProvider
from extraction.pipeline.domain.exceptions import AiUnavailableError
from extraction.pipeline.domain.ports import AiCallContext, AiReading
from idp_ocr_engines.model import PageImage

log = structlog.get_logger(__name__)

PATH = "/internal/v1/ai/field-read"


class GatewayFieldReader:
    def __init__(self, client: httpx.Client, tokens: ServiceTokenProvider) -> None:
        self._client = client  # base URL and timeouts are set by bootstrap
        self._tokens = tokens

    def read_fields(
        self, document_type: str, pages: Sequence[PageImage], keys: Sequence[str], context: AiCallContext
    ) -> AiReading:
        body = {
            "accountId": str(context.account_id),
            "priority": "PRIORITY" if context.priority else "STANDARD",
            "documentType": document_type,
            "pages": [{"bucket": ref.bucket, "key": ref.key} for ref in context.page_refs],
            "fields": [
                {"key": key, **({"label": context.labels[key]} if key in context.labels else {})} for key in keys
            ],
        }
        try:
            headers = {"Authorization": f"Bearer {self._tokens.token()}"}
            response = self._client.post(PATH, json=body, headers=headers)
        except (httpx.HTTPError, ServiceTokenError) as exc:
            log.warning("ai_gateway_unreachable", error=type(exc).__name__)
            raise AiUnavailableError("ai-gateway did not answer") from exc
        if response.status_code == httpx.codes.UNAUTHORIZED:
            self._tokens.invalidate()  # the next call fetches a new token
        if response.status_code != httpx.codes.OK:
            log.warning("ai_gateway_error", status=response.status_code, code=_code(response))
            raise AiUnavailableError(f"ai-gateway answered {response.status_code}")
        try:
            answer = response.json()
            values = {str(f["key"]): _text(f.get("value")) for f in answer["fields"]}
            return AiReading(
                values={key: values.get(key) for key in keys},
                model_id=str(answer["model"]),
                prompt_version=str(answer["promptVersion"]),
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise AiUnavailableError("ai-gateway's answer could not be read") from exc


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _code(response: httpx.Response) -> str | None:
    """The problem-details code, for the log; never the detail text."""
    try:
        code = response.json().get("code")
    except ValueError:
        return None
    return code if isinstance(code, str) else None
