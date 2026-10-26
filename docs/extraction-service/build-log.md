# extraction-service build log

What was built, why, and which rules it follows. The service is specified in IDP-03 ch. 2 and built to the Python
Engineering Standard. The conversion-service has its own log in `docs/conversion-service/build-log.md`.

## Entry 1: Sumukh's standardized OCR service merged, then cleaned up (2026-10-26)

**Goal:** bring the standardized OCR work from the `ocrtest` branch into `feat/idp-consolidation`, and fix what the
review found without sending it back.

### What came in (PR #7, from Sumukh)

The Flask prototype rebuilt as **extraction-service**: settings, logging, problem-details errors, health probes,
Dockerfile, `make check`. Pure field rules (PAN, Aadhaar with Verhoeff checksum and masking, dates, name similarity,
look-alike characters). The `idp_ocr_engines` library: one engine port, PP-OCRv5/v6 and Devanagari adapters, a
`models.yaml` registry with SHA-256 checks, CPU-only runs with timeouts. Quality gate, card cut-out, deskew and
orientation. PAN and Aadhaar templates, classification, two-engine agreement plus rule check, and the shared result
JSON with the Aadhaar number masked. Three ADRs. 417 tests, 98.7 % coverage on his machine.

**Review:** good. Masking covers the value, the normalized value and every engine's reading on every path; no field
values or OCR text are logged; the engine library imports nothing from the service, so conversion-service can reuse it.

### What the cleanup changed (`feat/extraction-service-cleanup`)

| Change | Why |
|---|---|
| `services/ocr/` → `services/extraction-service/` (`git mv`, history kept) | Matches `services/conversion-service/`. Cheap now, expensive once Docker, CI and docs point at it |
| Card images, `ocr_test_app/ground_truth.json` and `ocr_test_app/tests/test_api.py` no longer tracked; listed in `.gitignore`; local copies kept | They hold personal data: card photos, real names and ID numbers. The prototype API test asserts real card values, so it can only run beside the local images anyway |
| A PAN copied from the ground truth into `tests/unit/rule/test_pan.py` and `test_normalizers.py` replaced with made-up values (same look-alike positions) | No real ID numbers in tests |
| Indra OCR removed: adapter, its tests, the `indra` extra, the registry entry, slot C in `scripts/try_extract.py`. ADR 0002 → **Rejected for now (parked)** | Its weights' licence bars products that compete with Datalab's, carries over to our output, and can be restricted remotely. The project uses permissive licences only. The adapter stays on `ocrtest` |

The lock file lost Indra's dependencies too (`huggingface-hub`, PyYAML, tqdm and others), so the "no YAML library"
claim in ADR 0001 now holds for every extra.

### Consequence to know

With Indra gone, **Hindi fields have one reader** (PP-OCRv5 Devanagari in slot B). A field is accepted only when two
engines agree, so Hindi name and address come out `NEEDS_REVIEW` until a second Hindi engine with a permissive licence
is chosen (decision D-1). English fields are not affected.

### Try it

```bash
cd services/extraction-service
make check     # 376 tests, 97 % coverage; 26 skipped without the model files in OCR__MODELS_DIR
```

### Open

| Topic | Status |
|---|---|
| Personal data still in the repo | The same card numbers, and card images, are tracked under `research/benchmarks/`, `research/test-images/model-testing/id_cards/` and three `docs/mobile-extraction/` handovers, from the earlier mobile benchmarks. **Decision pending** |
| History on GitHub | Removing files from tracking doesn't remove them from history. Plan: push to the official GitLab without the GitHub history |
| CI and models | Tests that need models are skipped in CI until a step fetches them |
| Second Hindi engine | Part of D-1 |
