# IDP: mobile apps, services and research

Everything the team has built for IDP (Intelligent Document Processing): on-device Aadhaar/PAN extraction, OCR, document conversions, and the research behind them. It was consolidated onto one branch in October 2026 (see [docs/consolidation-plan.md](docs/consolidation-plan.md)).

## Layout

| Folder | What it is | Came from |
|---|---|---|
| `mobile/idp-android/` | Android app: Aadhaar/PAN extraction with an on-device model (llama.cpp) | `qwen4bmodel` (Karunya, Sumukh) |
| `mobile/idox-mobile/` | Android app: document conversion on the phone with 1–4B models, plus its reports and `memtest` | idox `IDP`/`testing` (Sumukh) |
| `services/conversions/` | Conversion scripts (`python_scripts/`), their HTTP API (`api/`), a local web app (`web/`), tests and docs | idox `testing` + `testing2` (Sumukh, Karunya) |
| `services/conversions/idocx_ai/` | iDocx AI features: workflow, forms, compare, translation, redaction, cleanup, accessibility. Kept inside `conversions/` for now, because the conversion tests read sample PDFs from it | idox (Prakyath) |
| `services/ocr/` | OCR test app (`ocr_test_app/`) and the Aadhaar/PAN test images it reads | `easyocr` (Sumukh) |
| `research/inference-engines/` | Inference engine experiments (llama.cpp, Hugging Face, vLLM) | `qwen4bmodel` |
| `research/stage1-contract/` | Prompt and output-contract experiments for extraction | `qwen4bmodel` |
| `research/benchmarks/` | Benchmark results, run logs and the `terminal_report.py` report script | `qwen4bmodel` |
| `research/test-images/` | Sample images and screenshots used in testing | `qwen4bmodel` |
| `docs/` | The consolidation plan, plus mobile-extraction findings, handovers and figures | various |

## Status

- **`services/` holds the existing code, not yet the finished services.** It doesn't follow the Python Engineering Standard yet (feature-first layout, `uv`, `ruff`, `mypy --strict`). That's the next phase, followed by the three IDP services: extraction-service, conversion-service and ai-gateway.
- Model weights, sample inputs and outputs, and build output are never committed (see `.gitignore`).
- The IDP specifications and the Python Engineering Standard are shared separately; they aren't in this repo.

## Quick checks

These need no model or GPU.

```bash
# Conversions: 155 tests
cd services/conversions
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests

# OCR test app: model inference skipped
cd services/ocr/ocr_test_app
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pypdfium2
OCR_SKIP_INFERENCE=1 .venv/bin/python -m unittest discover -s tests
```

The Android apps open in Android Studio from `mobile/idp-android/` or `mobile/idox-mobile/`. The native libraries in `app/src/main/jniLibs/` aren't in git; get them from the team.
