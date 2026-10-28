"""Read the fields of one Indian document (PAN, Aadhaar, driving licence); see doc_schemas.py.

One model call: the image (trimmed of its white margin, front and back stacked into one picture when
both are sent, shrunk to EXTRACT_EDGE) plus the prompt for that document type, answered as a JSON array
of values constrained by its schema and turned back into the keyed fields here.
"""

import base64
import io
import json
import os
import re

import httpx
from PIL import Image, ImageChops, ImageOps

import doc_schemas as S
import llm
from llm import ModelError

EXTRACT_EDGE = int(os.getenv("EXTRACT_EDGE", "1280"))   # longest side sent to the model
MIN_EDGE = int(os.getenv("MIN_EDGE", "768"))   # small images are enlarged to this: tiny print is misread
# Final scale on every image, after the limits above. Models cut images into pieces of different size
# (Qwen3/3.5: 32 px per token, Qwen2.5-VL: 28 px), so 0.875 gives a Qwen2.5-VL model the same number of
# image tokens a Qwen3.5 model reads at 1.
IMAGE_SCALE = float(os.getenv("IMAGE_SCALE", "1"))
SIDE_GAP = 24            # white strip between front and back when two images are stacked
MAX_TOKENS = 400
# llama-server reuses the work of an identical earlier prompt prefix (the prompt text, and the image if
# it is the same file). Keep on; false only to measure without any reuse.
CACHE_PROMPT = os.getenv("CACHE_PROMPT", "true").lower() != "false"
# Send the JSON schema for the model host to enforce while generating. llama-server does this exactly;
# mlx_vlm.server's enforcement corrupts the first value (always null), so it runs with false and relies on
# the prompt. Either way every value is checked against the schema here (_conforms).
SEND_SCHEMA = os.getenv("SEND_SCHEMA", "true").lower() != "false"


def trim_white(img: Image.Image, threshold: int = 235, pad: int = 8) -> Image.Image:
    """Crop away the white margin around the card(s). Returns img unchanged if nothing is non-white."""
    mask = ImageChops.invert(img.convert("L")).point(lambda v: 255 if v > 255 - threshold else 0)
    box = mask.getbbox()
    if not box:
        return img
    x0, y0, x1, y1 = box
    return img.crop((max(x0 - pad, 0), max(y0 - pad, 0), min(x1 + pad, img.width), min(y1 + pad, img.height)))


def _decode(raw: bytes, which: str) -> Image.Image:
    try:
        return ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert("RGB")
    except Exception:
        raise ModelError(400, "INVALID_IMAGE", f"Could not read the {which}. Send a JPEG, PNG or WebP file.")


def stack_sides(front: Image.Image, back: Image.Image) -> Image.Image:
    """Front above back, scaled to the same width, with a white strip between them."""
    width = max(front.width, back.width)
    sides = [s if s.width == width else s.resize((width, round(s.height * width / s.width)), Image.LANCZOS)
             for s in (front, back)]
    out = Image.new("RGB", (width, sides[0].height + SIDE_GAP + sides[1].height), "white")
    out.paste(sides[0], (0, 0))
    out.paste(sides[1], (0, sides[0].height + SIDE_GAP))
    return out


def prepare(raw: bytes, raw_back: bytes | None = None) -> tuple[str, tuple[int, int]]:
    """Decode (fixing phone rotation), trim the white margin, stack front and back when both are
    given, shrink to EXTRACT_EDGE; return (data_url, size)."""
    if raw_back is None:
        card = trim_white(_decode(raw, "image"))
    else:
        card = stack_sides(trim_white(_decode(raw, "front image")), trim_white(_decode(raw_back, "back image")))
    if max(card.size) > EXTRACT_EDGE:
        card.thumbnail((EXTRACT_EDGE, EXTRACT_EDGE), Image.LANCZOS)
    elif max(card.size) < MIN_EDGE:
        scale = MIN_EDGE / max(card.size)
        card = card.resize((round(card.width * scale), round(card.height * scale)), Image.LANCZOS)
    if IMAGE_SCALE != 1:
        card = card.resize((round(card.width * IMAGE_SCALE), round(card.height * IMAGE_SCALE)), Image.LANCZOS)
    buf = io.BytesIO()
    card.save(buf, format="JPEG", quality=92)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode(), card.size


def _clean(v):
    if not isinstance(v, str):
        return None
    return " ".join(v.split()) or None


def _conforms(schema: dict, v: str) -> bool:
    """Whether a cleaned value meets the field's schema (enum, pattern, maxLength)."""
    if "enum" in schema:
        return v in schema["enum"]
    for option in schema.get("anyOf", [schema]):
        if option.get("type") == "null":
            continue
        if "pattern" in option and not re.fullmatch(option["pattern"].strip("^$"), v):
            continue
        if len(v) > option.get("maxLength", len(v)):
            continue
        return True
    return False


def _parse(content: str):
    """The JSON answer, also when a model without enforced output wraps it in a ```json fence or writes a
    sentence before it (seen with Qwen3.5-9B on MLX)."""
    text = content.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        starts = [i for i in (text.find("["), text.find("{")) if i >= 0]
        if not starts:
            raise
        return json.JSONDecoder().raw_decode(text[min(starts):])[0]


def _as_list(values, keys: list[str]):
    """The values in field order. Models without enforced output sometimes answer with an object keyed by
    the field names instead of the array asked for; that is accepted when it has every field name."""
    if isinstance(values, dict) and all(k in values for k in keys):
        return [values[k] for k in keys]
    return values


async def extract(client: httpx.AsyncClient, doc_type: str, url: str) -> tuple[dict, dict]:
    """Return (fields, stats). Every key of the type is present; missing values are None."""
    body = {
        # Prompt text before the image: the text is the same for every request of this type, so the model
        # host keeps it cached and only reads the image fresh.
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": S.PROMPTS[doc_type]},
            {"type": "image_url", "image_url": {"url": url}}]}],
        "temperature": 0, "max_tokens": MAX_TOKENS,
        # Hybrid thinking models (Qwen3.5/3.6) answer directly instead of reasoning first. llama-server reads
        # chat_template_kwargs, mlx_vlm.server reads enable_thinking; each ignores the other.
        "chat_template_kwargs": {"enable_thinking": False},
        "enable_thinking": False,
        "cache_prompt": CACHE_PROMPT,
    }
    if SEND_SCHEMA:
        body["response_format"] = {"type": "json_schema",
                                   "json_schema": {"name": doc_type, "schema": S.SCHEMAS[doc_type]}}
    if llm.LLAMA_MODEL:
        body["model"] = llm.LLAMA_MODEL   # required by mlx_vlm.server, ignored by llama-server
    p = await llm.chat(client, body)
    try:
        values = _as_list(_parse(p["choices"][0]["message"]["content"]), list(S.FIELD_SCHEMAS[doc_type]))
    except (json.JSONDecodeError, KeyError, IndexError, TypeError, AttributeError):
        raise ModelError(502, "MODEL_OUTPUT_INVALID", "Model did not return valid JSON.")
    if not isinstance(values, list) or len(values) != len(S.FIELDS[doc_type]):
        raise ModelError(502, "MODEL_OUTPUT_INVALID", "Model did not return valid JSON.")
    fields = {}
    for (key, schema), v in zip(S.FIELD_SCHEMAS[doc_type].items(), values):
        v = _clean(v)
        fields[key] = v if v is not None and _conforms(schema, v) else None
    # llama-server reports token counts and times in "timings"; other servers give only "usage" counts.
    t, u = p.get("timings", {}), p.get("usage") or {}
    stats = {"prompt_tokens": t.get("prompt_n", u.get("prompt_tokens")), "prompt_ms": round(t.get("prompt_ms", 0)),
             "output_tokens": t.get("predicted_n", u.get("completion_tokens")),
             "output_ms": round(t.get("predicted_ms", 0))}
    return fields, stats
