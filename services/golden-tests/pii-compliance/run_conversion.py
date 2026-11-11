"""PII golden set, conversion part: conversion-service's real scanned-page converter reads a document picture. The page
text comes from the live model, using the gateway's page-read prompt, or from a stand-in that answers "OK" (a hijacked
reading). Logs and exception text are captured and searched.

Run after run_gateway.py, with conversion's environment (``make pii`` does): ``python run_conversion.py``. Exit code 1 on any leak.
"""

import base64
import contextlib
import io
import json
import pathlib
import sys
import tempfile
import traceback
from pathlib import Path
from uuid import uuid4

import httpx

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]  # services/
RESULTS = HERE / "results"
RESULTS.mkdir(exist_ok=True)
sys.path.insert(0, str(HERE.parent))  # services/golden-tests: the shared modules
sys.path.insert(0, str(HERE))
from documents import DOCS  # noqa: E402
from model import CHAT_URL as MODEL_URL  # noqa: E402
from textmatch import leaks  # noqa: E402

sys.path.insert(0, str(REPO / "conversion-service" / "src"))
from conversion.conversion.domain.document_model import Page, TextBlock  # noqa: E402
from conversion.conversion.domain.formats import Representation  # noqa: E402
from conversion.conversion.domain.options import ConversionOptions  # noqa: E402
from conversion.conversion.domain.ports import PageReaderPort, StepContext, StepRequest  # noqa: E402
from conversion.conversion.domain.route import Converter, Step  # noqa: E402
from conversion.conversion.infrastructure.converters.ocr_to_model import ModelFirstOcrConverter  # noqa: E402
from conversion.conversion.infrastructure.readers.model_reader import to_page  # noqa: E402
from conversion.core.config import LoggingSettings  # noqa: E402
from conversion.core.logging import configure_logging  # noqa: E402

SYSTEM = (REPO / "ai-gateway/src/ai_gateway/prompts/page-read.v3.md").read_text().split("---\n", 2)[2].strip()
SCHEMA = json.loads((RESULTS / "schema_page_read.json").read_text())
QUESTION = (
    "Count the paragraphs, then extract every block on this page, from the first line to the last line. "
    'Answer with a JSON object: "analysis" first, then "blocks".'
)


class LiveReader:
    def read_page(self, image: pathlib.Path, number: int, context: StepContext) -> Page:  # noqa: ARG002
        url = "data:image/png;base64," + base64.b64encode(image.read_bytes()).decode()
        body = {
            "messages": [
                {"role": "system", "content": SYSTEM},
                {
                    "role": "user",
                    "content": [{"type": "text", "text": QUESTION}, {"type": "image_url", "image_url": {"url": url}}],
                },
            ],
            "temperature": 0,
            "max_tokens": 4000,
            "chat_template_kwargs": {"enable_thinking": False},
            "response_format": {"type": "json_schema", "json_schema": {"name": "page_read", "schema": SCHEMA}},
        }
        r = httpx.post(MODEL_URL, json=body, timeout=600)
        r.raise_for_status()
        return to_page(json.loads(r.json()["choices"][0]["message"]["content"])["blocks"])


class OkReader:
    def read_page(self, image: pathlib.Path, number: int, context: StepContext) -> Page:  # noqa: ARG002
        return Page(blocks=(TextBlock(text="OK"),))


def converter(reader: PageReaderPort) -> ModelFirstOcrConverter:
    return ModelFirstOcrConverter(reader, render_dpi=72, max_pages=5, max_pixels=50_000_000)


rows = []
for doc in ("P05_letter", "P02_aadhaar"):
    spec = DOCS[doc]
    readers: tuple[tuple[str, PageReaderPort], ...] = (
        ("live model", LiveReader()),
        ("hijacked reading ('OK')", OkReader()),
    )
    for label, reader in readers:
        print(f"[conversion] {doc} {label}", flush=True)
        buf, error = io.StringIO(), None
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(buf):
            configure_logging(LoggingSettings(level="DEBUG", format="json"))
            step = Step(converter=Converter.OCR_TO_MODEL, source=Representation.PDF, target=Representation.DOCUMENT_MODEL)
            out = pathlib.Path(tmp) / "out"
            out.mkdir()
            request = StepRequest(
                step=step,
                inputs=(HERE / "pages" / f"{doc}.png",),
                output_dir=out,
                options=ConversionOptions(),
                context=StepContext(account_id=uuid4(), key_prefix="acct/job/item"),
            )
            try:
                converter(reader).convert(request)
            except Exception as exc:  # noqa: BLE001 - the point is to read what an error says
                error = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
        logs = buf.getvalue()
        rows.append(
            {
                "doc": doc,
                "run": label,
                "outcome": (error or "ok").split(":")[0],
                "log_lines": len(logs.splitlines()),
                "events": sorted({json.loads(line).get("event") for line in logs.splitlines() if line.startswith("{")}),
                "id_in_logs": leaks(logs, spec["id"]),
                "other_in_logs": leaks(logs, spec["other"]),
                "id_in_error": leaks(error or "", spec["id"]),
                "other_in_error": leaks(error or "", spec["other"]),
            }
        )
(RESULTS / "conversion.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
leaked = [r for r in rows if r["id_in_logs"] or r["other_in_logs"] or r["id_in_error"] or r["other_in_error"]]
print(f"conversion: {len(rows)} runs, {len(leaked)} with a leak in logs or errors")
sys.exit(1 if leaked else 0)
