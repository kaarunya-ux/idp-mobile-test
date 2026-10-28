"""Connection to the model host: a llama-server running a vision-language model, reached over HTTP.

The model does not run inside this service. LLAMA_URL points at a machine started with
model_host/start_model_server.sh (see README.md). Any OpenAI-compatible server that supports
response_format json_schema works (llama-server, vLLM).
"""

import logging
import os

import httpx

log = logging.getLogger("uvicorn.error")
LLAMA_URL = os.getenv("LLAMA_URL", "http://127.0.0.1:8081").rstrip("/")
LLAMA_API_KEY = os.getenv("LLAMA_API_KEY") or None
LLAMA_TIMEOUT_S = float(os.getenv("LLAMA_TIMEOUT_S", "120"))
# Model id sent with each request. llama-server ignores it; servers that host several models
# (mlx_vlm.server) require it.
LLAMA_MODEL = os.getenv("LLAMA_MODEL") or None


class ModelError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def make_client() -> httpx.AsyncClient:
    headers = {"Authorization": f"Bearer {LLAMA_API_KEY}"} if LLAMA_API_KEY else {}
    return httpx.AsyncClient(base_url=LLAMA_URL, headers=headers,
                             timeout=httpx.Timeout(LLAMA_TIMEOUT_S, connect=5))


async def chat(client: httpx.AsyncClient, body: dict) -> dict:
    """POST an OpenAI-style chat request to the model host; map failures to API errors.
    Callers get a generic message; the details (host, upstream reply) go to the server log only."""
    try:
        r = await client.post("/v1/chat/completions", json=body)
    except httpx.TimeoutException:
        log.warning("Model host %s timed out", LLAMA_URL)
        raise ModelError(504, "MODEL_TIMEOUT", "The model took too long to answer.")
    except httpx.HTTPError as e:
        log.warning("Model host %s not reachable: %s", LLAMA_URL, e)
        raise ModelError(503, "MODEL_UNAVAILABLE", "The model service is not reachable.")
    if r.status_code != 200:
        log.warning("Model host %s returned %s: %s", LLAMA_URL, r.status_code, r.text[:300])
        raise ModelError(502, "MODEL_ERROR", "The model service returned an error.")
    return r.json()


async def is_healthy(client: httpx.AsyncClient) -> bool:
    try:
        return (await client.get("/health", timeout=3)).status_code == 200
    except httpx.HTTPError:
        return False


async def model_name(client: httpx.AsyncClient) -> str | None:
    """The model id the host reports (its --alias, or the loaded one when it lists several), or None
    when unreachable."""
    try:
        r = await client.get("/v1/models", timeout=3)
        if r.status_code != 200:
            return None
        models = r.json()["data"]
        return next((m["id"] for m in models if m.get("loaded")), models[0]["id"])
    except (httpx.HTTPError, KeyError, IndexError, ValueError):
        return None
