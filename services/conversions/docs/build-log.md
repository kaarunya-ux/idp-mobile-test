# Build log: idox-conversion-service

The model-first conversions of `services/conversions`, brought to the Python Engineering Standard in place (the prototype
scripts stay where they are and are moved into `src/idox_conversion/` step by step; the old tests stay under `tests/`).
Branch: the current one; nothing is committed until the owner says so.

| Step | What | Rules |
|---|---|---|
| 1 ✔ | Skeleton: `pyproject.toml`, Makefile, settings, structured logging, error codes, ids, clock, health, ADRs 0001 and 0002 | CFG-01, ERR-05, OBS-01..03, ML-07, PY-01 |
| 2 ✔ | Domain: formats, route planner (30 matrix pairs), document model (page → blocks → lines / tables), thresholds from settings | DOM-01, FLW-01 |
| 3 ✔ (registry file open) | Model adapter: request and answer schemas, versioned prompts, thinking sent every time, timeouts, no content in logs; model registry | LLM-01..06, ML-01, ML-04, ML-06 |
| 4 deferred by the owner | Replace PyMuPDF with pypdfium2 and pdfplumber | DEP-02, ADR 0002 |
| 5 | Converters: office to PDF (LibreOffice, timeout, no macros), page images, the readers and the writers, the checks and repairs | ASY-03, ch. 3.7 of IDP-03 |
| 6 | Workers and messages: `ConversionRequested` intake, Celery pools, outbox | ch. 16 |
| 7 | Tests: the 30 matrix pairs on golden inputs, import-linter contracts, coverage 80% | ch. 21, ML-12 |

Deferred for the owner: whether the model is called directly or through the AI gateway (ADR 0001, "Open"); the test page and
the API in `web/` and `api/` are test tools that the IDP-03 service does not have.

## Findings that shaped the settings

- A model server restarted with thinking on answered every page with hidden reasoning first. The 4000-token budget was used
  up before any output, and 15 of 32 conversions failed. `MODEL__THINKING` is now sent on every request (ML-06).
- Smoke test of 32 conversions, one sample per source (thinking off): 30 OK, 2 review, 0 failed; words kept 99.2% on average,
  table cells matched 98.0%; 9 to 175 s per model conversion.

## Status (steps 1 to 3 done; `make check` green: ruff, mypy --strict, 3 import-linter contracts, 109 tests, 95% coverage)

- Step 2: `conversion/domain` (formats, route, document model, check policies) and `conversion/application/plan_route.py`
  (27 conversions; every other pair is `CNV-422-ROUTE-001`). The check rules that were `_tokens` and `check_against_source` in
  `office_model_convert.py` are pure functions with the thresholds passed in (ML-07).
- Step 3 (ports are in `domain/ports.py`): `conversion/infrastructure/model` (prompt loader, answer schema, `LlamaServerPageReader`). Prompts live in
  `prompts/*.v1.md` (LLM-02); thinking is sent on every request; retries only for a refused connection or 502/503/504; an
  answer cut off at the limit or not matching the schema is an error (LLM-03); logs carry no document text (LLM-06).
- Dockerfile (targets `api` and `worker`) and `compose.yaml` follow standard ch. 23 and the tree in ch. 3.2; not built here (no Docker on this machine).
- **Open (ML-01):** `models.yaml` is not written. The model runs on a remote llama.cpp server, so its file's SHA-256 is not
  something this service can verify; the registry entry needs the owner's decision (record the server's reported model id
  instead, or a checksum supplied by whoever runs the server). Name and version are recorded on every reading (ML-04).
- Step 4 (replace PyMuPDF) is deferred by the owner. Until then step 5 keeps calling PyMuPDF from the digital-PDF stages, which
  ADR 0002 lists as an exception.
Every addition beyond IDP-03 and the standard is listed in ADR 0003; nothing new is added without the owner's say.
