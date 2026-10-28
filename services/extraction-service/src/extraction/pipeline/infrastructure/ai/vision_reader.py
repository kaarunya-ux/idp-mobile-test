"""``AiFieldReaderPort`` with a vision model on the model host (ported from the web extraction app's ``llm.py`` and
``extract.py``).

One request per page: the card image, the type's prompt, and its JSON schema so the host can only answer in the
right shape (llama-server turns the schema into a grammar). Every value is checked against its schema again here,
because not every host enforces it. Anything that isn't a usable answer raises ``AiUnavailableError``, so the
pipeline leaves the fields for review.

Kept as the web app's backup for laptops without ai-gateway: bootstrap uses it only when ``ENVIRONMENT=local`` and
``AI_GATEWAY__URL`` is unset (ADR 0006). Everywhere else ``GatewayFieldReader`` is used (IDP-03 ch. 4: only
ai-gateway talks to the model server). The gateway has its own copy of the prompts.
"""

import base64
import io
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

import httpx
import structlog
from PIL import Image

from extraction.pipeline.domain.exceptions import AiUnavailableError
from extraction.pipeline.domain.ports import AiCallContext, AiReading
from extraction.pipeline.infrastructure.ai.card_prompts import PROFILES, PROMPT_VERSION, CardProfile
from idp_ocr_engines.model import PageImage

log = structlog.get_logger(__name__)

_MAX_TOKENS = 512
_JPEG_QUALITY = 90
_SIDE_GAP_PX = 24  # white strip between a card's front and back
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


class VisionModelFieldReader:
    def __init__(self, client: httpx.Client, *, model: str | None, max_edge_px: int) -> None:
        self._client = client  # base URL, key and timeouts are set by bootstrap
        self._model = model
        self._max_edge_px = max_edge_px

    def read_fields(
        self, document_type: str, pages: Sequence[PageImage], keys: Sequence[str], context: AiCallContext
    ) -> AiReading:
        """``context`` is not needed here: the pages are sent as bytes, and there is no queue or cap."""
        profile = PROFILES.get(document_type)
        if profile is None:
            raise AiUnavailableError(f"no AI profile for {document_type}")
        answer = _values(self._ask(profile, _stack_sides(pages)), profile)
        wanted = set(keys)
        values = {
            key: answer.get(name) for name, key in profile.field_keys.items() if key is not None and key in wanted
        }
        return AiReading(values=values, model_id=self._model or "vision-model", prompt_version=PROMPT_VERSION)

    def _ask(self, profile: CardProfile, page: Image.Image) -> list[object]:
        body: dict[str, Any] = {
            "messages": [
                {
                    "role": "user",
                    "content": [  # the prompt first: it is the same for every card, so the host keeps it cached
                        {"type": "text", "text": profile.full_prompt},
                        {"type": "image_url", "image_url": {"url": self._data_url(page)}},
                    ],
                }
            ],
            "temperature": 0,
            "max_tokens": _MAX_TOKENS,
            "response_format": {"type": "json_schema", "json_schema": {"name": "fields", "schema": profile.schema}},
            "chat_template_kwargs": {"enable_thinking": False},  # hybrid models answer directly
            "cache_prompt": True,
        }
        if self._model:
            body["model"] = self._model
        try:
            response = self._client.post("/v1/chat/completions", json=body)
        except httpx.HTTPError as exc:  # timeout or unreachable: the details go to the log, not the result
            log.warning("ai_fallback_unreachable", error=type(exc).__name__)
            raise AiUnavailableError("the model host did not answer") from exc
        if response.status_code != httpx.codes.OK:
            log.warning("ai_fallback_error", status=response.status_code)
            raise AiUnavailableError(f"the model host answered {response.status_code}")
        try:
            content = response.json()["choices"][0]["message"]["content"]
            return _as_list(_parse(content), list(profile.fields))
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise AiUnavailableError("the model's answer was not the requested JSON") from exc

    def _data_url(self, page: Image.Image) -> str:
        image = page.copy()
        image.thumbnail((self._max_edge_px, self._max_edge_px), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=_JPEG_QUALITY)
        return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()


def _stack_sides(pages: Sequence[PageImage]) -> Image.Image:
    """Front above back, at the same width, with a white strip between (the web app's ``stack_sides``): the model
    reads both sides of a card in one request."""
    sides = [Image.frombytes("RGB", (p.width, p.height), p.rgb) for p in pages[:2]]
    if len(sides) == 1:
        return sides[0]
    width = max(side.width for side in sides)
    sides = [
        side
        if side.width == width
        else side.resize((width, round(side.height * width / side.width)), Image.Resampling.LANCZOS)
        for side in sides
    ]
    stacked = Image.new("RGB", (width, sides[0].height + _SIDE_GAP_PX + sides[1].height), "white")
    stacked.paste(sides[0], (0, 0))
    stacked.paste(sides[1], (0, sides[0].height + _SIDE_GAP_PX))
    return stacked


def _parse(content: str) -> object:
    """The JSON answer, also when a host without enforced output wraps it in a ```json fence or adds a sentence."""
    text = content.strip()
    fenced = _FENCE.search(text)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        starts = [index for index in (text.find("["), text.find("{")) if index >= 0]
        if not starts:
            raise
        return json.JSONDecoder().raw_decode(text[min(starts) :])[0]


def _as_list(values: object, names: list[str]) -> list[object]:
    """The values in field order; an object keyed by every field name is accepted too."""
    if isinstance(values, dict) and all(name in values for name in names):
        values = [values[name] for name in names]
    if not isinstance(values, list) or len(values) != len(names):
        raise ValueError("wrong number of values")
    return values


def _values(values: list[object], profile: CardProfile) -> dict[str, str | None]:
    result: dict[str, str | None] = {}
    for (name, schema), raw in zip(profile.fields.items(), values, strict=True):
        value = " ".join(raw.split()) if isinstance(raw, str) else None
        if value and _conforms(schema, value):
            result[name] = profile.values.get(name, {}).get(value, value)
        else:
            result[name] = None
    return result


def _conforms(schema: Mapping[str, Any], value: str) -> bool:
    """Whether a value meets its schema (enum, pattern, maxLength)."""
    if "enum" in schema:
        return value in schema["enum"]
    for option in schema.get("anyOf", [schema]):
        if option.get("type") == "null":
            continue
        if "pattern" in option and not re.fullmatch(option["pattern"].strip("^$"), value):
            continue
        if len(value) > option.get("maxLength", len(value)):
            continue
        return True
    return False
