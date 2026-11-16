"""The model server the golden tests talk to: an OpenAI-style llama.cpp server with vision on (``--mmproj``).

``GOLDEN_MODEL_URL`` is the server's base URL and must be set (there is no default): the address of the model host
changes, and an old default would measure the wrong server. Check it with ``curl <url>/models``.
"""

import base64
import io
import os
import time

import httpx
from PIL import Image

_URL = os.environ.get("GOLDEN_MODEL_URL")
if not _URL:  # no default: a model host's address changes (it has moved twice), and a stale default measures the wrong server
    raise SystemExit("Set GOLDEN_MODEL_URL to the model server's base URL, for example http://model-host:8080/v1")
BASE_URL = _URL.rstrip("/")
CHAT_URL = f"{BASE_URL}/chat/completions"


def ask(system: str | None, text: str, image: Image.Image, schema: dict, max_tokens: int, name: str = "answer") -> dict:
    """One call: the picture and the text, the answer held to ``schema``. Returns ok, content, seconds, tokens_out."""
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    url = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()
    messages = ([{"role": "system", "content": system}] if system else []) + [
        {"role": "user", "content": [{"type": "text", "text": text}, {"type": "image_url", "image_url": {"url": url}}]}
    ]
    body = {
        "messages": messages,
        "temperature": 0,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_schema", "json_schema": {"name": name, "schema": schema}},
        "chat_template_kwargs": {"enable_thinking": False},
    }
    start = time.time()
    try:
        response = httpx.post(CHAT_URL, json=body, timeout=900)
        response.raise_for_status()
        data = response.json()
        return {
            "ok": True,
            "content": data["choices"][0]["message"]["content"],
            "seconds": round(time.time() - start, 1),
            "tokens_out": (data.get("usage") or {}).get("completion_tokens"),
            "server_fingerprint": data.get("system_fingerprint"),
        }
    except Exception as exc:  # noqa: BLE001 - a golden run records any failure instead of stopping
        return {"ok": False, "content": "", "error": type(exc).__name__, "seconds": round(time.time() - start, 1)}
