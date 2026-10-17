"""Qwen3-VL behind a local llama-server: process management, image prep, prompt, extraction."""

import asyncio
import base64
import io
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import httpx
from PIL import Image, ImageOps

from card_crop import crop_to_card

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent

# Which model the API runs. Change this one word to switch ("4b" or "8b"), then restart the API
# (see HANDOVER.md). switch_model.sh sets the same thing through the ACTIVE_MODEL environment variable.
# card_min_edge: cropped cards smaller than this are enlarged; the 4B needs it to read small print
# (card_14's email was misspelled at the natural ~740px crop, correct at 1024px).
MODELS = {
    "4b": {"model": "models/Qwen3VL-4B-Instruct-Q4_K_M.gguf",
           "mmproj": "models/mmproj-Qwen3VL-4B-Instruct-F16.gguf", "card_min_edge": 1024},
    "8b": {"model": "models/Qwen3VL-8B-Instruct-Q4_K_M.gguf",
           "mmproj": "models/mmproj-Qwen3VL-8B-Instruct-F16.gguf", "card_min_edge": 0},
}
ACTIVE_MODEL = os.getenv("ACTIVE_MODEL") or "4b"
_active = MODELS[ACTIVE_MODEL]

MODEL_PATH = Path(os.getenv("MODEL_PATH", REPO / _active["model"]))
MMPROJ_PATH = Path(os.getenv("MMPROJ_PATH", REPO / _active["mmproj"]))
LLAMA_BIN = os.getenv("LLAMA_SERVER_BIN", shutil.which("llama-server") or "llama-server")
LLAMA_PORT = int(os.getenv("LLAMA_PORT", "8081"))
LLAMA_URL = f"http://127.0.0.1:{LLAMA_PORT}"
# Long-edge caps. A cropped card is capped at 1024px: the test crops were 675-940px and read 58/64
# fields; a 12 MP phone photo would otherwise crop to a ~2,400px card (~1,500 image tokens, slower
# than the uncropped 1600px photo). When no card is found the whole photo is capped at 1600px.
CARD_MAX_EDGE = int(os.getenv("CARD_MAX_EDGE", "1024"))
# A cropped card whose long edge is under this is enlarged to it; 0 leaves crops at natural size.
CARD_MIN_EDGE = int(os.getenv("CARD_MIN_EDGE", _active["card_min_edge"]))
MAX_IMAGE_EDGE = int(os.getenv("MAX_IMAGE_EDGE", "1600"))
MODEL_NAME = MODEL_PATH.stem
LOG_DIR = ROOT / "logs"

SYSTEM_PROMPT = """You read photos of business (visiting) cards and extract contact details.
The photo may show the card lying on a desk, at an angle, or with glare. Read only the card itself.

Return one JSON object with exactly these keys:
- name: the person's full name exactly as printed. Never the company name, a job title, or a name that is part of the address (a street, avenue, road or building named after someone, such as "Dr. ... Avenue"). null if no person's name is printed.
- email: the person's email address, copied character by character exactly as printed; never drop, add or swap a letter. If several are printed, prefer the person's own over a generic one (info@, contact@, sales@). null if none.
- phone: one phone number exactly as printed, keeping the country code, brackets and spaces. If a number labelled Mobile, Mob, M or Cell is printed, return that one, even when a Telephone, Tel or Office number is printed above it. Otherwise return the first number shown. Never a fax number. null if none.
- address: the full postal address exactly as printed, every line joined into one line separated by ", ". Keep building, street, area, city, postal code or PIN, and country when printed. A company name on its own is not an address: an address must contain a street, building, area, P.O. Box, city or postal code. null if no address is printed.

Rules:
- Copy characters exactly as printed. Never correct spelling, translate, reformat, or complete a partial value.
- If a value is printed in English and in another script, use the English one. If it is printed only in another script, copy it as printed.
- If a value is not printed on the card or cannot be read, use null. Never guess or invent a value."""

USER_TEXT = "Extract the contact details from this visiting card."

FIELDS = ("name", "email", "phone", "address")

# Turned into a grammar by llama-server, so the model can only emit these four keys in this order.
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": ["string", "null"], "maxLength": 120},
        "email": {"type": ["string", "null"], "maxLength": 120},
        "phone": {"type": ["string", "null"], "maxLength": 60},
        "address": {"type": ["string", "null"], "maxLength": 400},
    },
    "required": list(FIELDS),
    "additionalProperties": False,
}


class ModelError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def prepare_image(raw: bytes) -> tuple[str, int, int, bool]:
    """Decode, fix phone rotation (EXIF), crop to the card, cap the long edge, re-encode as a JPEG
    data URL. Cropping drops the desk: on the 16 test photos it cut image tokens from ~1,165 to
    300-540 and the average request from 11.5s to 6.1s with no loss of fields (see card_crop.py)."""
    try:
        img = Image.open(io.BytesIO(raw))
        img = ImageOps.exif_transpose(img).convert("RGB")
    except Exception:
        raise ModelError(400, "INVALID_IMAGE", "Could not read the image. Send a JPEG, PNG or WebP file.")
    img, cropped = crop_to_card(img)
    edge = CARD_MAX_EDGE if cropped else MAX_IMAGE_EDGE
    if max(img.size) > edge:
        img.thumbnail((edge, edge), Image.LANCZOS)
    elif cropped and max(img.size) < CARD_MIN_EDGE:
        scale = CARD_MIN_EDGE / max(img.size)
        img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92)
    url = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    return url, img.width, img.height, cropped


# Country code split, done in code so it is deterministic and never invents a code that is not printed.
# "+44 20 7946 0958", "(+44) 20...", "0044 20...", "971 (50) 1234567", "91 9876543210" all split;
# "0120 1234567" (local, trunk 0) and "9876543210" (no code) keep country_code null.
_CC_PLUS = re.compile(r"^\(?\s*(?:\+|00)\s*(\d{1,3})(?:\s*\)\s*|[\s\-./]+)(\S.*)$")
_CC_BARE = re.compile(r"^([1-9]\d{0,2})[\s\-.]+(\(?\d.*)$")
_CC_GLUED = re.compile(r"^\+(\d{8,15})$")
# Only for "+919876543210" (no separator after the code). Country codes are prefix-free, so at most
# one of these can match. Common codes only; anything else keeps the whole number in phone.
_KNOWN_CC = {
    "1", "7", "20", "27", "30", "31", "32", "33", "34", "36", "39", "40", "41", "43", "44", "45", "46",
    "47", "48", "49", "52", "55", "60", "61", "62", "63", "64", "65", "66", "81", "82", "84", "86", "90",
    "91", "92", "93", "94", "95", "98", "212", "213", "216", "234", "244", "254", "255", "256", "258",
    "260", "263", "351", "353", "880", "960", "961", "962", "963", "964", "965", "966", "967", "968",
    "970", "971", "972", "973", "974", "975", "976", "977",
}


def split_country_code(phone: str | None) -> tuple[str | None, str | None]:
    if not phone:
        return None, None
    p = phone.strip()
    m = _CC_PLUS.match(p)
    if not m:
        m = _CC_BARE.match(p)
        if m and len(re.sub(r"\D", "", m.group(2))) < 8:
            m = None                       # "120-1234567": a local area code, not a country code
    if m:
        return "+" + m.group(1), m.group(2).strip(" -./")
    g = _CC_GLUED.match(p)
    if g:
        digits = g.group(1)
        for n in (1, 2, 3):
            if digits[:n] in _KNOWN_CC:
                return "+" + digits[:n], digits[n:]
    return None, p


def _clean(value):
    if not isinstance(value, str):
        return None
    value = " ".join(value.split())
    return value or None


async def extract(client: httpx.AsyncClient, image_url: str) -> tuple[dict, dict]:
    body = {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": image_url}},
                {"type": "text", "text": USER_TEXT},
            ]},
        ],
        "temperature": 0,
        "max_tokens": 300,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "visiting_card", "schema": RESPONSE_SCHEMA},
        },
    }
    try:
        r = await client.post("/v1/chat/completions", json=body)
    except httpx.TimeoutException:
        raise ModelError(504, "MODEL_TIMEOUT", "The model took too long to answer.")
    except httpx.HTTPError as e:
        raise ModelError(503, "MODEL_UNAVAILABLE", f"Model server is not reachable: {e}")
    if r.status_code != 200:
        raise ModelError(502, "MODEL_ERROR", f"Model server returned {r.status_code}: {r.text[:300]}")

    payload = r.json()
    text = payload["choices"][0]["message"]["content"]
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        raise ModelError(502, "MODEL_OUTPUT_INVALID", f"Model did not return valid JSON: {text[:300]}")

    fields = {k: _clean(parsed.get(k)) for k in FIELDS}
    if fields["email"]:
        fields["email"] = fields["email"].replace(" ", "")
    country_code, phone = split_country_code(fields["phone"])
    data = {"name": fields["name"], "email": fields["email"], "country_code": country_code,
            "phone": phone, "address": fields["address"]}
    t = payload.get("timings", {})
    stats = {
        "prompt_tokens": t.get("prompt_n"),
        "prompt_ms": round(t.get("prompt_ms", 0)),
        "output_tokens": t.get("predicted_n"),
        "output_ms": round(t.get("predicted_ms", 0)),
    }
    return data, stats


# --- llama-server process -------------------------------------------------------------

async def is_healthy(client: httpx.AsyncClient) -> bool:
    try:
        return (await client.get("/health", timeout=2)).status_code == 200
    except httpx.HTTPError:
        return False


def start_server() -> subprocess.Popen:
    for p in (MODEL_PATH, MMPROJ_PATH):
        if not p.exists():
            raise RuntimeError(f"Missing model file: {p}")
    LOG_DIR.mkdir(exist_ok=True)
    args = [
        LLAMA_BIN,
        "-m", str(MODEL_PATH), "--mmproj", str(MMPROJ_PATH),
        "--host", "127.0.0.1", "--port", str(LLAMA_PORT),
        "-ngl", "99", "-fa", "on",
        # One request at a time; ~1,150 image tokens + ~400 prompt + ~80 output fits easily.
        "-c", "4096", "-np", "1",
        # Keep the weights pinned in RAM so an idle Mac never pages them out between requests.
        "--load-mode", "mmap+mlock", "--lazy-mode", "off",
        "--no-webui",
    ]
    log = open(LOG_DIR / "llama-server.log", "a")
    log.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} {' '.join(args)}\n")
    log.flush()
    return subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT)


async def wait_until_healthy(client: httpx.AsyncClient, proc: subprocess.Popen, timeout: float = 300):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"llama-server exited with code {proc.returncode}; see {LOG_DIR / 'llama-server.log'}")
        if await is_healthy(client):
            return
        await asyncio.sleep(0.5)
    raise RuntimeError("llama-server did not become ready in time")


async def warm_up(client: httpx.AsyncClient):
    """One throwaway request so the vision encoder is initialised and the system prompt is cached."""
    buf = io.BytesIO()
    Image.new("RGB", (320, 192), "white").save(buf, format="JPEG")
    await extract(client, "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode())
