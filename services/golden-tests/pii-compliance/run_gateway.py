"""PII golden set, ai-gateway part: the gateway's real services read each document with the live model while logs,
exception text and Prometheus metrics are captured and searched for the document's personal data.

Run with the gateway's environment (``make pii`` does): ``python run_gateway.py``. Exit code 1 on any leak.
"""

import asyncio
import base64
import contextlib
import io
import json
import os
import sys
import traceback
from pathlib import Path

import httpx
from prometheus_client import generate_latest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]  # services/
RESULTS = HERE / "results"
RESULTS.mkdir(exist_ok=True)
sys.path.insert(0, str(HERE.parent))  # services/golden-tests: the shared modules
sys.path.insert(0, str(HERE))
from documents import DOCS  # noqa: E402

from model import CHAT_URL as MODEL_URL  # noqa: E402
from textmatch import leaks  # noqa: E402

sys.path[:0] = [str(REPO / "ai-gateway" / "src"), str(REPO / "ai-gateway" / "tests")]
from ai_gateway.ai.application.answer_models import PageReadAnswer, schema_of  # noqa: E402
from ai_gateway.ai.application.runner import ModelCallRunner  # noqa: E402
from ai_gateway.ai.application.services.read_fields import ReadFieldsService  # noqa: E402
from ai_gateway.ai.application.services.read_page import ReadPageService  # noqa: E402
from ai_gateway.ai.domain.card_profiles import PROFILES  # noqa: E402
from ai_gateway.ai.domain.models import FieldReadQuery, FieldSpec, Image, ImageRef, ModelReply, Task, Tokens  # noqa: E402
from ai_gateway.ai.infrastructure.metrics import PrometheusMetrics  # noqa: E402
from ai_gateway.ai.infrastructure.prompt_files import PromptFiles  # noqa: E402
from ai_gateway.core.config import LoggingSettings  # noqa: E402
from ai_gateway.core.logging import configure_logging  # noqa: E402
from support.fakes import FakeCaps, FakeSlots, FixedClock, caller  # noqa: E402


class LiveModel:
    """The live model. mode: ok | invalid_first (the first answer is cut, so the runner repairs) | invalid_always."""

    def __init__(self, mode: str) -> None:
        self.mode, self.calls = mode, 0

    async def complete(self, request, *, timeout_s):  # noqa: ANN001, ARG002
        content = [{"type": "text", "text": request.text}] + [
            {
                "type": "image_url",
                "image_url": {"url": f"data:{i.media_type};base64,{base64.b64encode(i.data).decode()}"},
            }
            for i in request.images
        ]
        body = {
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": content},
                *({"role": r, "content": t} for r, t in request.follow_up),
            ],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            "response_format": {"type": "json_schema", "json_schema": {"name": "x", "schema": request.schema}},
            "chat_template_kwargs": {"enable_thinking": False},
        }
        async with httpx.AsyncClient(timeout=600) as client:
            r = await client.post(MODEL_URL, json=body)
        r.raise_for_status()
        text = r.json()["choices"][0]["message"]["content"]
        self.calls += 1
        if self.mode == "invalid_always" or (self.mode == "invalid_first" and self.calls == 1):
            text = text[: len(text) // 2]  # cut in half: not valid JSON, and it still holds the document's values
        return ModelReply(model="live", content=text, tokens=Tokens(input=0, output=0))


class LiveImages:
    async def load(self, refs, *, stack, max_side_px=None, tidy=False):  # noqa: ANN001, ARG002
        return [Image(data=(HERE / "pages" / f"{refs[0].key}.png").read_bytes(), media_type="image/png")]


METRICS = PrometheusMetrics()


def make_runner(mode: str) -> ModelCallRunner:
    return ModelCallRunner(
        model=LiveModel(mode),
        slots=FakeSlots(),
        caps=FakeCaps(),
        prompts=PromptFiles.load(),
        metrics=METRICS,
        clock=FixedClock(),
        timeouts=dict.fromkeys(Task, 600.0),
    )


async def read(doc: str, mode: str):
    spec = DOCS[doc]
    runner = make_runner(mode)
    ref = ImageRef(bucket="b", key=doc)
    if spec["kind"] == "card":
        keys = [k for k in PROFILES[spec["type"]].field_keys.values() if k]
        service = ReadFieldsService(runner=runner, images=LiveImages(), prompts=PromptFiles.load())
        answer = await service.read(caller(), FieldReadQuery(document_type=spec["type"], pages=[ref], fields=[FieldSpec(key=k) for k in keys]))
        return {v.key: v.value for v in answer.value}
    service = ReadPageService(runner=runner, images=LiveImages(), prompts=PromptFiles.load(), max_side_px=2048, min_block_share=0.5)
    answer = await service.read(caller(), ref)
    return {"blocks": len(answer.value.blocks)}


async def main() -> int:
    (RESULTS / "schema_page_read.json").write_text(json.dumps(schema_of(PageReadAnswer)), encoding="utf-8")
    rows, values = [], {}
    for doc, spec in DOCS.items():
        for mode in ("ok", "invalid_first", "invalid_always"):
            print(f"[gateway] {doc} {mode}", flush=True)
            buf = io.StringIO()
            error = result = None
            with contextlib.redirect_stdout(buf):
                configure_logging(LoggingSettings(level="DEBUG", format="json"))
                try:
                    result = await read(doc, mode)
                except Exception as exc:  # noqa: BLE001 - the point is to read what an error says
                    error = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
            logs = buf.getvalue()
            if os.environ.get("GOLDEN_LOG_DIR"):  # keep the real log lines: input for drift/logwatch.py
                log_dir = Path(os.environ["GOLDEN_LOG_DIR"])
                log_dir.mkdir(parents=True, exist_ok=True)
                (log_dir / f"{doc}_{mode}.jsonl").write_text(logs, encoding="utf-8")
            if mode == "ok" and spec["kind"] == "card":
                values[doc] = result
            rows.append(
                {
                    "doc": doc,
                    "mode": mode,
                    "outcome": "error" if error else "ok",
                    "log_lines": len(logs.splitlines()),
                    "id_in_logs": leaks(logs, spec["id"]),
                    "other_in_logs": leaks(logs, spec["other"]),
                    "id_in_error": leaks(error or "", spec["id"]),
                    "other_in_error": leaks(error or "", spec["other"]),
                    "error_kind": (error or "").split(":")[0] or None,
                }
            )
    metrics = generate_latest().decode()
    every_id = [t for s in DOCS.values() for t in s["id"]]
    every_other = [t for s in DOCS.values() for t in s["other"]]
    out = {
        "rows": rows,
        "metrics_bytes": len(metrics),
        "id_in_metrics": leaks(metrics, every_id),
        "other_in_metrics": leaks(metrics, every_other),
    }
    (RESULTS / "gateway.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    (RESULTS / "gateway_values.json").write_text(json.dumps(values, indent=1), encoding="utf-8")
    leaked = [r for r in rows if r["id_in_logs"] or r["other_in_logs"] or r["id_in_error"] or r["other_in_error"]]
    leaked_metrics = out["id_in_metrics"] or out["other_in_metrics"]
    print(f"gateway: {len(rows)} runs, {len(leaked)} with a leak in logs or errors, metrics leak: {bool(leaked_metrics)}")
    return 1 if leaked or leaked_metrics else 0


sys.exit(asyncio.run(main()))
