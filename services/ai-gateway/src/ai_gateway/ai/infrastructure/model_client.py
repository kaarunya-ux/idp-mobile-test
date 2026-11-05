"""``ModelPort`` on the local model server: OpenAI-style ``POST /chat/completions`` (IDP-03 ch. 4.3).

The answer's JSON schema goes in ``response_format``, so a host that enforces schemas (llama-server turns it into a
grammar) can only answer in that shape; the application validates it again anyway. ``MODEL__SEND_SCHEMA=false`` leaves
it out, for a host whose enforcement is broken (mlx_vlm.server corrupts the first value; the prompts say the shape
too, and a wrong answer gets one repair attempt). Hybrid models' thinking is switched by the prompt, or else by
``MODEL__THINKING`` (off by default: these tasks want the answer, not the reasoning), sent both as llama.cpp's
``chat_template_kwargs`` and as mlx_vlm.server's ``enable_thinking``; each server ignores the other's.

Timeouts, connection errors, 5xx and 429 count as failures for the circuit breaker; a 4xx is our mistake, so it doesn't
open the breaker.
"""

import base64
from collections.abc import Mapping
from typing import Any

import httpx
import structlog

from ai_gateway.ai.domain.exceptions import ModelUnavailableError
from ai_gateway.ai.domain.models import ModelReply, ModelRequest, ModelRole, Tokens
from ai_gateway.ai.infrastructure.breaker import CircuitBreaker

log = structlog.get_logger(__name__)


class OpenAiModelClient:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        models: Mapping[ModelRole, str],
        breaker: CircuitBreaker,
        send_schema: bool = True,
        thinking: bool | None = None,
    ) -> None:
        self._client = client  # base URL (…/v1), key and connect timeout are set in bootstrap
        self._models = models
        self._breaker = breaker
        self._send_schema = send_schema
        self._thinking = thinking  # when the prompt doesn't say

    async def complete(self, request: ModelRequest, *, timeout_s: float) -> ModelReply:
        self._breaker.before_call()
        try:
            response = await self._client.post(
                "chat/completions",
                json=self._body(request),
                timeout=httpx.Timeout(timeout_s, connect=self._client.timeout.connect),
            )
        except httpx.HTTPError as exc:  # timeout or unreachable: the details go to the log, not the caller
            self._breaker.failed()
            log.warning("model_unreachable", error=type(exc).__name__, role=request.role.value)
            raise ModelUnavailableError from exc
        if (
            response.status_code >= httpx.codes.INTERNAL_SERVER_ERROR
            or response.status_code == httpx.codes.TOO_MANY_REQUESTS
        ):
            self._breaker.failed()
            log.warning("model_error", status=response.status_code, role=request.role.value)
            raise ModelUnavailableError
        if response.status_code != httpx.codes.OK:
            self._breaker.succeeded()  # the server is up; it refused this request
            log.error("model_refused_request", status=response.status_code, role=request.role.value)
            raise ModelUnavailableError("The model server refused the request.")
        self._breaker.succeeded()
        return _reply(response, self._models[request.role])

    def _body(self, request: ModelRequest) -> dict[str, Any]:
        body = _body(request, self._models[request.role])
        thinking = request.thinking if request.thinking is not None else self._thinking
        if thinking is not None:
            body["chat_template_kwargs"] = {"enable_thinking": thinking}  # llama.cpp
            body["enable_thinking"] = thinking  # mlx_vlm.server
        if self._send_schema:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": request.schema_name, "schema": dict(request.schema), "strict": True},
            }
        return body


class RoutedModel:
    """``ModelPort`` over several servers: a role with a server of its own goes there, every other role to the
    default (the page role on the larger model, ADR 0005). Each server has its own circuit breaker."""

    def __init__(self, default: OpenAiModelClient, by_role: Mapping[ModelRole, OpenAiModelClient]) -> None:
        self._default = default
        self._by_role = by_role

    async def complete(self, request: ModelRequest, *, timeout_s: float) -> ModelReply:
        return await self._by_role.get(request.role, self._default).complete(request, timeout_s=timeout_s)


def _body(request: ModelRequest, model: str) -> dict[str, Any]:
    content: list[dict[str, Any]] = [{"type": "text", "text": request.text}]  # text first: the host caches it
    content += [
        {"type": "image_url", "image_url": {"url": f"data:{i.media_type};base64,{base64.b64encode(i.data).decode()}"}}
        for i in request.images
    ]
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": request.system},
        {"role": "user", "content": content},
        *({"role": role, "content": text} for role, text in request.follow_up),
    ]
    return {
        "model": model,
        "messages": messages,
        "temperature": request.temperature,
        "max_tokens": request.max_tokens,
    }


def _reply(response: httpx.Response, model: str) -> ModelReply:
    try:
        body = response.json()
        content = body["choices"][0]["message"]["content"]
        usage = body.get("usage") or {}
        tokens = Tokens(input=int(usage.get("prompt_tokens", 0)), output=int(usage.get("completion_tokens", 0)))
        return ModelReply(model=str(body.get("model") or model), content=str(content or ""), tokens=tokens)
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        log.warning("model_reply_unreadable")
        raise ModelUnavailableError("The model server's reply could not be read.") from exc
