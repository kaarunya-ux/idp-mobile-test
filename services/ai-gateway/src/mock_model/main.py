"""The mock model server: OpenAI-style ``POST /v1/chat/completions`` with recorded or schema-shaped answers.

    uvicorn mock_model.main:app --port 4000        # then MODEL__BASE_URL=http://localhost:4000/v1

For laptops and CI only (IDP-03 ch. 5). It reads nothing from the request but the schema name and the question text,
keeps nothing, and logs only the schema name. Never deploy it next to real documents: it is not a model.
"""

import asyncio
import json
from typing import Any

import structlog
from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict
from pydantic_settings import BaseSettings, SettingsConfigDict

from mock_model.recordings import find_recording, load_recordings
from mock_model.schema_answer import answer_for_schema

log = structlog.get_logger(__name__)


class MockSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MOCK_MODEL__", extra="ignore")

    latency_ms: int = 0  # pretend to think, to see queueing and timeouts on a laptop


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str = "mock"
    messages: list[dict[str, Any]]
    response_format: dict[str, Any] | None = None


def create_app(settings: MockSettings | None = None) -> FastAPI:
    settings = settings or MockSettings()
    recordings = load_recordings()
    app = FastAPI(title="mock-model", version="0.1.0")

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "UP"}

    @app.get("/v1/models")
    async def models() -> dict[str, Any]:
        return {"object": "list", "data": [{"id": name, "object": "model"} for name in ("vl", "smart", "auto")]}

    @app.post("/v1/chat/completions")
    async def complete(request: ChatRequest) -> dict[str, Any]:
        if settings.latency_ms:
            await asyncio.sleep(settings.latency_ms / 1000)
        schema_format = (request.response_format or {}).get("json_schema", {})
        name, schema = str(schema_format.get("name", "")), schema_format.get("schema", {})
        text = _question(request.messages)
        recorded = find_recording(recordings, name, text)
        answer = recorded.answer if recorded is not None else answer_for_schema(schema)
        content = json.dumps(answer, ensure_ascii=False)
        log.info("mock_answer", schema=name, recorded=recorded is not None)
        return {
            "id": "mock-completion",
            "object": "chat.completion",
            "model": f"mock-{request.model}",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": len(text) // 4, "completion_tokens": len(content) // 4},
        }

    return app


def _question(messages: list[dict[str, Any]]) -> str:
    """The first user turn's text (the repair turns that follow don't change the answer)."""
    for message in messages:
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content
        return " ".join(str(p.get("text", "")) for p in content or [] if p.get("type") == "text")
    return ""


app = create_app()
