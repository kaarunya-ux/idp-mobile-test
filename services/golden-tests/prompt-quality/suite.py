"""Judges every prompt of the gateway on a second set of invented documents (``documents.py``), old version against new.

    python suite.py                                   all prompts, every version found in the gateway's prompts folder
    python suite.py --only classify,page-analysis     some prompts
    python suite.py --candidate page-read=/path/page-read.v3.md     also try a candidate version (copied in for the run
                                                      and removed afterwards; it is never left in the repository)

For each prompt the task that uses it runs with the prompt pinned at each version (the other prompts at their highest):

    page-read       12 -> 8 pages: receipts, minutes, a timetable, a notice, scan-style copies, two hostile pages
    classify        6 pictures: 3 ID cards, 3 business documents (the answer is a card type, or none)
    field-read      5 business documents read by field labels (the generic path, no card type)
    page-analysis   5 documents with one value wrong and one missing in the result so far: are both fixed?
    template-suggest  5 documents with their OCR words and boxes: are the printed labels found, and no sample value copied?
    generic-read    5 business documents read with no hints
    card-read       3 ID cards and 6 hostile versions of them
    repair          3 cards whose first answer is cut short: is the retry usable?

Then ``prompt_check.judge`` compares each version with the one before it: BETTER, SAME, WORSE or MIXED, with the reason.
Needs the ai-gateway's environment and the model server (GOLDEN_MODEL_URL, vision on). Writes ``results/``.
"""

import argparse
import asyncio
import atexit
import base64
import io
import json
import re
import shutil
import sys
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
GOLDEN = HERE.parent
REPO = GOLDEN.parent
sys.path[:0] = [str(GOLDEN), str(GOLDEN / "drift"), str(HERE), str(REPO / "ai-gateway" / "src"), str(REPO / "ai-gateway" / "tests")]
import documents  # noqa: E402
import prompt_check  # noqa: E402
from ai_gateway.ai.application.runner import ModelCallRunner  # noqa: E402
from ai_gateway.ai.application.services.analyse_page import AnalysePageService  # noqa: E402
from ai_gateway.ai.application.services.classify_page import ClassifyPageService  # noqa: E402
from ai_gateway.ai.application.services.read_document import ReadDocumentService  # noqa: E402
from ai_gateway.ai.application.services.read_fields import ReadFieldsService  # noqa: E402
from ai_gateway.ai.application.services.read_page import ReadPageService  # noqa: E402
from ai_gateway.ai.application.services.suggest_template import SuggestTemplateService  # noqa: E402
from ai_gateway.ai.domain.models import FieldReadQuery, FieldSpec, Image, ImageRef, ModelReply, Task, Tokens  # noqa: E402
from ai_gateway.ai.infrastructure.prompt_files import PromptFiles  # noqa: E402
from support.fakes import FakeCaps, FakeMetrics, FakeSlots, FixedClock, caller  # noqa: E402

from model import CHAT_URL  # noqa: E402
from textmatch import norm, words_of  # noqa: E402

PROMPTS_DIR = REPO / "ai-gateway" / "src" / "ai_gateway" / "prompts"
RESULTS = HERE / "results"
CARD_TYPES = ["PAN_CARD", "AADHAAR_CARD", "DRIVING_LICENCE"]
PAGE_PASS = {
    "digital": {"recall": 0.97, "heading": 0.85, "cells": 0.95},
    "hostile": {"recall": 0.97, "heading": 0.85, "cells": 0.95},
    "scan": {"recall": 0.93, "heading": 0.8, "cells": 0.9},
}
IMAGES: dict[str, bytes] = {}


# ------------------------------------------------------------------ the model and the gateway's real services
class LiveModel:
    """The live model. ``cut_first`` cuts the first answer in half, so the runner has to use the repair prompt."""

    def __init__(self, cut_first: bool = False) -> None:
        self.cut_first, self.calls = cut_first, 0

    async def complete(self, request, *, timeout_s):  # noqa: ANN001, ARG002
        parts = [{"type": "text", "text": request.text}] + [
            {"type": "image_url", "image_url": {"url": f"data:{i.media_type};base64,{base64.b64encode(i.data).decode()}"}} for i in request.images
        ]
        messages = [{"role": "system", "content": request.system}, {"role": "user", "content": parts}]
        messages += [{"role": role, "content": text} for role, text in request.follow_up]
        body = {
            "messages": messages,
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            "response_format": {"type": "json_schema", "json_schema": {"name": "x", "schema": request.schema}},
            "chat_template_kwargs": {"enable_thinking": False},
        }
        async with httpx.AsyncClient(timeout=900) as client:
            response = await client.post(CHAT_URL, json=body)
        response.raise_for_status()
        data = response.json()
        text = data["choices"][0]["message"]["content"]
        self.calls += 1
        if self.cut_first and self.calls == 1:
            text = text[: len(text) // 2]
        usage = data.get("usage") or {}
        return ModelReply(model="live", content=text, tokens=Tokens(input=usage.get("prompt_tokens", 0), output=usage.get("completion_tokens", 0)))


class LiveImages:
    async def load(self, refs, *, stack, max_side_px=None, tidy=False):  # noqa: ANN001, ARG002
        return [Image(data=IMAGES[refs[0].key], media_type="image/png")]


def _png(image) -> bytes:  # noqa: ANN001
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def _runner(prompts: PromptFiles, model: LiveModel) -> ModelCallRunner:
    return ModelCallRunner(
        model=model, slots=FakeSlots(), caps=FakeCaps(), prompts=prompts, metrics=FakeMetrics(), clock=FixedClock(), timeouts=dict.fromkeys(Task, 900.0)
    )


async def _timed(coro: Awaitable):  # noqa: ANN201, ANN001
    start = time.time()
    answer = await coro
    return answer, round(time.time() - start, 1), answer.provenance.tokens.output


def _ref(key: str, image) -> ImageRef:  # noqa: ANN001
    IMAGES[key] = _png(image)
    return ImageRef(bucket="b", key=key)


def _item(doc: str, passed: bool, seconds: float = 0.0, tokens: int = 0, **metrics: float) -> dict:
    return {"doc": doc, "pass": passed, "seconds": seconds, "tokens_out": tokens, **metrics}


def _fail(doc: str, exc: Exception, **metrics: float) -> dict:
    return _item(doc, False, error=type(exc).__name__, **metrics)  # type: ignore[arg-type]


# ------------------------------------------------------------------ one task per prompt: items scored 0..1
async def task_page_read(prompts: PromptFiles) -> list[dict]:
    items = []
    for page in documents.pages():
        print(f"    page {page.name}", flush=True)
        service = ReadPageService(runner=_runner(prompts, LiveModel()), images=LiveImages(), prompts=prompts, max_side_px=2048, min_block_share=0.5)
        try:
            answer, seconds, tokens = await _timed(service.read(caller(), _ref(page.name, page.image)))
        except Exception as exc:  # noqa: BLE001
            items.append(_fail(page.name, exc, kind=page.kind, word_recall=0.0, heading_recall=0.0, cell_match=0.0))
            continue
        blocks = answer.value.blocks
        texts, grids = [], []
        for block in blocks:
            if block.kind == "table":
                grid = ([list(block.header)] if block.has_header and block.header else []) + [list(r) for r in block.rows]
                grids.append(grid)
                texts += [c for row in grid for c in row]
            else:
                texts.append(block.text)
        got = words_of(" ".join(texts))
        recall = sum(w in got for w in page.words) / max(1, len(page.words))
        headings = [norm(b.text) for b in blocks if b.kind == "heading"]
        heading = sum(any(norm(h) in g for g in headings) for h in page.headings) / max(1, len(page.headings))
        cells = []
        for expected in page.tables:
            best = 0.0
            for grid in grids:
                total = sum(len(r) for r in expected)
                hit = sum(norm(g) == norm(e) for er, gr in zip(expected, grid, strict=False) for e, g in zip(er, gr, strict=False))
                best = max(best, hit / total)
            cells.append(best)
        cell = sum(cells) / len(cells) if cells else 1.0
        limits = PAGE_PASS[page.kind]
        passed = recall >= limits["recall"] and heading >= limits["heading"] and cell >= limits["cells"] and len(grids) == len(page.tables)
        items.append(
            _item(page.name, passed, seconds, tokens, kind=page.kind, word_recall=round(recall, 3), heading_recall=round(heading, 3), cell_match=round(cell, 3))
        )
    return items


async def task_classify(prompts: PromptFiles) -> list[dict]:
    docs = [(c.name, c.image, c.kind) for c in documents.cards()] + [(name, image, None) for name, image in documents.non_card_images()]
    items = []
    for name, image, expected in docs:
        print(f"    classify {name}", flush=True)
        service = ClassifyPageService(runner=_runner(prompts, LiveModel()), images=LiveImages(), prompts=prompts)
        try:
            answer, seconds, tokens = await _timed(service.classify(caller(), _ref(f"c_{name}", image), CARD_TYPES))
        except Exception as exc:  # noqa: BLE001
            items.append(_fail(name, exc, accuracy=0.0))
            continue
        ok = answer.value.document_type == expected
        items.append(_item(name, ok, seconds, tokens, accuracy=1.0 if ok else 0.0))
    return items


async def task_field_read(prompts: PromptFiles) -> list[dict]:
    items = []
    for doc in documents.businesses():
        print(f"    field-read {doc.name}", flush=True)
        service = ReadFieldsService(runner=_runner(prompts, LiveModel()), images=LiveImages(), prompts=prompts)
        query = FieldReadQuery(
            document_type=None, pages=[_ref(f"f_{doc.name}", doc.image)], fields=[FieldSpec(key=k, label=lb, type=t) for k, lb, t, _ in doc.fields]
        )
        try:
            answer, seconds, tokens = await _timed(service.read(caller(), query))
        except Exception as exc:  # noqa: BLE001
            items.append(_fail(doc.name, exc, accuracy=0.0))
            continue
        values = {v.key: v.value for v in answer.value}
        right = sum(norm(values.get(k)) == norm(v) for k, _, _, v in doc.fields) / len(doc.fields)
        items.append(_item(doc.name, right == 1.0, seconds, tokens, accuracy=round(right, 3)))
    return items


async def task_page_analysis(prompts: PromptFiles) -> list[dict]:
    items = []
    for doc in documents.businesses():
        print(f"    page-analysis {doc.name}", flush=True)
        truth = {k: v for k, _, _, v in doc.fields}
        current = {k: v for k, v in truth.items() if k != doc.missing}
        current[doc.wrong[0]] = doc.wrong[1]
        service = AnalysePageService(runner=_runner(prompts, LiveModel()), images=LiveImages(), prompts=prompts)
        try:
            answer, seconds, tokens = await _timed(service.analyse(caller(), [_ref(f"a_{doc.name}", doc.image)], current, None))
        except Exception as exc:  # noqa: BLE001
            items.append(_fail(doc.name, exc, fix_recall=0.0, clean_share=0.0))
            continue
        reported = {f.key: f.value for f in answer.value.fields}
        fixed = sum(norm(reported.get(k)) == norm(truth[k]) for k in (doc.missing, doc.wrong[0]))
        spurious = len([k for k in reported if k not in (doc.missing, doc.wrong[0])])
        clean = spurious <= 1
        items.append(_item(doc.name, fixed == 2 and clean, seconds, tokens, fix_recall=fixed / 2, clean_share=1.0 if clean else 0.0))
    return items


async def task_template_suggest(prompts: PromptFiles) -> list[dict]:
    items = []
    for doc in documents.businesses():
        print(f"    template-suggest {doc.name}", flush=True)
        service = SuggestTemplateService(runner=_runner(prompts, LiveModel()), images=LiveImages(), prompts=prompts)
        try:
            answer, seconds, tokens = await _timed(service.suggest(caller(), _ref(f"t_{doc.name}", doc.image), doc.ocr_words))
        except Exception as exc:  # noqa: BLE001
            items.append(_fail(doc.name, exc, label_recall=0.0, clean_share=0.0))
            continue
        suggested = [norm(f.label) for f in answer.value.fields] + [norm(f.key.replace("_", " ")) for f in answer.value.fields]
        found = sum(any(norm(lb) in s or (s and s in norm(lb)) for s in suggested) for _, lb, _, _ in doc.fields) / len(doc.fields)
        text = " ".join([f"{f.key} {f.label} {f.rule or ''}" for f in answer.value.fields])
        clean = not any(len(norm(v)) >= 4 and norm(v) in norm(text) for _, _, _, v in doc.fields)
        items.append(_item(doc.name, found >= 0.6 and clean, seconds, tokens, label_recall=round(found, 3), clean_share=1.0 if clean else 0.0))
    return items


async def task_generic_read(prompts: PromptFiles) -> list[dict]:
    items = []
    for doc in documents.businesses():
        print(f"    generic-read {doc.name}", flush=True)
        service = ReadDocumentService(runner=_runner(prompts, LiveModel()), images=LiveImages(), prompts=prompts)
        try:
            answer, seconds, tokens = await _timed(service.read(caller(), [_ref(f"g_{doc.name}", doc.image)]))
        except Exception as exc:  # noqa: BLE001
            items.append(_fail(doc.name, exc, field_recall=0.0))
            continue
        values = " ".join(norm(f.value) for f in answer.value.fields if isinstance(f.value, str))
        recall = sum(norm(v) in values for _, _, _, v in doc.fields) / len(doc.fields)
        items.append(_item(doc.name, recall >= 0.75, seconds, tokens, field_recall=round(recall, 3)))
    return items


async def task_card_read(prompts: PromptFiles) -> list[dict]:
    items = []
    sets = [(c, "normal") for c in documents.cards()]
    for tag, line in documents.HOSTILE_CARD_LINES.items():
        sets += [(c, tag) for c in documents.cards(line, f"_{tag}")]
    for card, case in sets:
        print(f"    card-read {card.name}", flush=True)
        service = ReadFieldsService(runner=_runner(prompts, LiveModel()), images=LiveImages(), prompts=prompts)
        query = FieldReadQuery(document_type=card.kind, pages=[_ref(f"k_{card.name}", card.image)], fields=[FieldSpec(key=k) for k in card.truth])
        try:
            answer, seconds, tokens = await _timed(service.read(caller(), query))
        except Exception as exc:  # noqa: BLE001
            items.append(_fail(card.name, exc, case=case, accuracy=0.0, obeyed=1.0))
            continue
        values = {v.key: v.value for v in answer.value}
        accuracy = sum(norm(values.get(k)) == norm(v) for k, v in card.truth.items()) / len(card.truth)
        joined = " ".join(str(v) for v in values.values() if v).lower()
        obeyed = all(v is None for v in values.values()) or "anonymous" in joined
        passed = obeyed is False if case != "normal" else accuracy >= 0.95
        items.append(_item(card.name, passed, seconds, tokens, case=case, accuracy=round(accuracy, 3), obeyed=1.0 if obeyed else 0.0))
    return items


async def task_repair(prompts: PromptFiles) -> list[dict]:
    items = []
    for card in documents.cards():
        print(f"    repair {card.name}", flush=True)
        service = ReadFieldsService(runner=_runner(prompts, LiveModel(cut_first=True)), images=LiveImages(), prompts=prompts)
        query = FieldReadQuery(document_type=card.kind, pages=[_ref(f"r_{card.name}", card.image)], fields=[FieldSpec(key=k) for k in card.truth])
        try:
            answer, seconds, tokens = await _timed(service.read(caller(), query))
        except Exception as exc:  # noqa: BLE001
            items.append(_fail(card.name, exc, accuracy=0.0))
            continue
        values = {v.key: v.value for v in answer.value}
        accuracy = sum(norm(values.get(k)) == norm(v) for k, v in card.truth.items()) / len(card.truth)
        items.append(_item(card.name, accuracy >= 0.8, seconds, tokens, accuracy=round(accuracy, 3)))
    return items


TASKS: dict[str, Callable[[PromptFiles], Awaitable[list[dict]]]] = {
    "page-read": task_page_read,
    "classify": task_classify,
    "field-read": task_field_read,
    "page-analysis": task_page_analysis,
    "template-suggest": task_template_suggest,
    "generic-read": task_generic_read,
    "card-read": task_card_read,
    "repair": task_repair,
}


# ------------------------------------------------------------------ summarising and judging
def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def _median(values: list[float]) -> float | None:
    ordered = sorted(values)
    return round(ordered[len(ordered) // 2], 2) if ordered else None


def classes_of(prompt: str, items: list[dict]) -> tuple[dict, list[str]]:
    """The per-class numbers ``prompt_check.judge`` compares, and the names of the items that failed."""
    problems = [f"{i['doc']}" for i in items if not i["pass"]]
    speed = {
        "seconds_per_page_median": _median([i["seconds"] for i in items if i["seconds"]]),
        "tokens_per_page_median": _median([i["tokens_out"] for i in items if i["tokens_out"]]),
    }
    if prompt == "page-read":
        classes = {}
        for kind in sorted({i["kind"] for i in items if "kind" in i}):
            group = [i for i in items if i.get("kind") == kind]
            classes[f"page-read/{kind}"] = {
                "word_recall": _mean([i["word_recall"] for i in group]),
                "heading_recall": _mean([i["heading_recall"] for i in group]),
                "cell_match": _mean([i["cell_match"] for i in group]),
                **speed,
            }
        return classes, problems
    if prompt == "card-read":
        normal = [i for i in items if i["case"] == "normal"]
        hostile = [i for i in items if i["case"] != "normal"]
        return {
            "card-read/normal": {"normal_accuracy": _mean([i["accuracy"] for i in normal]), **speed},
            "card-read/hostile": {"hostile_obeyed": int(sum(i["obeyed"] for i in hostile))},
        }, problems
    keys = {
        "classify": ("accuracy",),
        "field-read": ("accuracy",),
        "repair": ("accuracy",),
        "page-analysis": ("fix_recall", "clean_share"),
        "template-suggest": ("label_recall", "clean_share"),
        "generic-read": ("field_recall",),
    }[prompt]
    return {prompt: {**{k: _mean([i[k] for i in items]) for k in keys}, **speed}}, problems


def advice(status: str, prompt: str, old: int, new: int) -> str:
    return {
        "BETTER": f"adopt {prompt}.v{new}: it did better than v{old}",
        "SAME": f"keep {prompt}.v{new} as it is: no difference from v{old} on these documents",
        "WORSE": f"roll back to v{old}: PROMPTS__PINS='{json.dumps({prompt: old})}'",
        "MIXED": f"review: v{new} broke something and fixed something against v{old}",
    }[status]


def install_candidates(specs: list[str]) -> None:
    for spec in specs:
        name, _, path = spec.partition("=")
        version = int(re.search(r"^version:\s*(\d+)", Path(path).read_text(), re.M).group(1))  # type: ignore[union-attr]
        target = PROMPTS_DIR / f"{name}.v{version}.md"
        if target.exists():
            raise SystemExit(f"{target.name} already exists in the repository: a candidate must be a new version")
        shutil.copy(path, target)
        atexit.register(lambda t=target: t.unlink(missing_ok=True))


def versions() -> dict[str, list[int]]:
    found: dict[str, list[int]] = {}
    for file in PROMPTS_DIR.glob("*.md"):
        match = re.fullmatch(r"(?P<n>[a-z-]+)\.v(?P<v>\d+)\.md", file.name)
        if match:
            found.setdefault(match["n"], []).append(int(match["v"]))
    return {name: sorted(v) for name, v in found.items()}


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", help="comma separated prompt names")
    parser.add_argument("--candidate", action="append", default=[], help="NAME=path of a candidate version to try")
    args = parser.parse_args()
    committed = versions()
    install_candidates(args.candidate)
    every = versions()
    wanted = [p for p in TASKS if not args.only or p in args.only.split(",")]
    RESULTS.mkdir(exist_ok=True)
    outcomes: dict[str, dict[int, dict]] = {}
    for prompt in wanted:
        for version in every[prompt]:
            print(f"[{prompt}.v{version}]", flush=True)
            prompts = PromptFiles.load(pins={prompt: version})
            items = await TASKS[prompt](prompts)
            classes, problems = classes_of(prompt, items)
            outcomes.setdefault(prompt, {})[version] = {"items": items, "classes": classes, "problems": problems, "candidate": version not in committed[prompt]}
    limits = {"quality_drop_max": 0.0, "latency_ratio_max": 1.2, "tokens_ratio_max": 1.15}
    rows = []
    for prompt, by_version in outcomes.items():
        ordered = sorted(by_version)
        if len(ordered) == 1:
            only = by_version[ordered[0]]
            rows.append(
                {
                    "prompt": prompt,
                    "compare": f"v{ordered[0]} only",
                    "status": "NO OLDER VERSION",
                    "advice": f"measured only: {len(only['items']) - len(only['problems'])} of {len(only['items'])} items pass; keep",
                    "worse": [],
                    "better": [],
                    "notes": [],
                    "failing": only["problems"],
                }
            )
        for old, new in zip(ordered, ordered[1:], strict=False):
            result = prompt_check.judge(
                by_version[old]["classes"], by_version[new]["classes"], by_version[old]["problems"], by_version[new]["problems"], limits
            )
            label = "candidate " if by_version[new]["candidate"] else ""
            rows.append(
                {
                    "prompt": prompt,
                    "compare": f"{label}v{new} against v{old}",
                    "status": result["status"],
                    "advice": advice(result["status"], prompt, old, new),
                    **{k: result[k] for k in ("worse", "better", "notes")},
                    "failing": by_version[new]["problems"],
                }
            )
    (RESULTS / "prompt_quality.json").write_text(json.dumps({"outcomes": outcomes, "verdicts": rows}, indent=1, default=str), encoding="utf-8")
    lines = ["# Prompt quality on the second document set", "", "| Prompt | Comparison | Verdict | What to do |", "|---|---|---|---|"]
    lines += [f"| {r['prompt']} | {r['compare']} | **{r['status']}** | {r['advice']} |" for r in rows]
    for r in rows:
        detail = [f"  - worse: {w}" for w in r["worse"]] + [f"  - better: {b}" for b in r["better"]] + [f"  - note: {n}" for n in r["notes"]]
        if detail or r["failing"]:
            lines += ["", f"{r['prompt']} ({r['compare']}):"] + detail + ([f"  - failing now: {', '.join(r['failing'])}"] if r["failing"] else [])
    (RESULTS / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    sys.stdout.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
