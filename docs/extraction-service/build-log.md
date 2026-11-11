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

## Entry 2: The service runs: messaging, storage, the database and the pipeline (steps E1 and E2, 2026-10-28)

**Goal:** extraction-service takes jobs from Java and returns results, as conversion-service does. The pipeline
Sumukh standardized now runs inside a worker.

### What we did

```
core/                db.py, inbox.py, outbox.py (copied from conversion-service); config: DB__, RABBIT__, CELERY__,
                     STORAGE__, OUTBOX__, AI_FALLBACK__; errors gain `retryable`; EXT-504-PIPELINE-001
pipeline/domain/     jobs.py (request, item and its rules, events); ports for storage, page images, the unit of
                     work, the task queue and the AI reader; decisions: FieldSource.AI and decide_with_ai
pipeline/application accept_extraction.py (intake), run_extraction.py (the worker); extract_document.py gains the
                     AI step (AiAssist)
pipeline/infrastructure/
  ai/                card_prompts.py, vision_reader.py: the web extraction app's prompts and model client, ported
  messaging/         ExtractionRequested in; JobProgressed / ExtractionCompleted / WorkFailed out (IDP-00 ch. 7.6)
  persistence/       extraction_item table, repository, unit of work
  storage/           MinIO: read inputs, write page images and result.json to idp-derived
  tasks.py           Celery: one task on the `pipeline` queue
bootstrap.py, consumers.py, worker.py, outbox_relay.py   the processes
migrations/          0001_baseline: inbox_message, outbox_event, extraction_item
Dockerfile           targets api, service, worker (models mounted at /models)
tests/               about 55 new unit tests; tests/support/job_fakes.py
```

### How a job runs

```
Java ─ExtractionRequested─▶ idp.commands ─▶ intake ─▶ extraction_db (inbox + item) ─▶ Celery `pipeline` queue
worker ─▶ MinIO (the file) ─▶ SCAN · QUALITY_CHECK · PREPARE ─▶ CLASSIFY ─▶ READ (A ∥ B) ─▶ VERIFY
       ─▶ AI_ASSIST (unsettled fields only) ─▶ ASSEMBLE ─▶ idp-derived/<account>/<document>/<item>/{page-N.png,result.json}
relay  ─▶ idp.events: JobProgressed per stage, then ExtractionCompleted {resultKey, documentType, templateRef, summary, usage}
```

Duplicate, retry and stale-worker rules are conversion-service's (agreed with Java): the same `eventId` is recorded
once; a completed item asked again gets its stored result; a failed one is queued again.

### OCR first, AI as fallback

The pipeline's decision per field doesn't change: A and B must agree and the rule must pass. Fields still unsettled
go to the AI reader **in one request per document**. An AI value is accepted only if the same rule passes (PAN
format, Aadhaar checksum, date, ...), with source `AI`; otherwise the field stays NEEDS_REVIEW and keeps the AI's
reading for the reviewer. If the model host is down, the job still succeeds with those fields for review.

The AI reader is the web extraction app's code (prompts, JSON schemas, answer checks), ported: synchronous, settings
instead of environment reads, its field names mapped onto the templates' (`parent_name` -> `father_name`, `F` ->
`FEMALE`). It calls the model host directly until ai-gateway exists (ADR 0004).

### Decisions and open questions

| Topic | Decision |
|---|---|
| AI fallback | Through a port; today the model host directly (ADR 0004), ai-gateway later. Off unless `AI_FALLBACK__URL` is set |
| AI confidence | 0.9 for a value that passes its rule (`AI_FALLBACK__CONFIDENCE`). **Open:** set from an evaluation (ML-07) |
| One task, not a canvas | The pipeline runs in one Celery task; A and B read in threads (ADR 0003 item 10). A canvas is a scaling change |
| Second file | Prepared and stored as page 1; fields are read from the front. Two-sided reading is step E3 |
| `templateId` | Accepted and stored; classification picks among the standard templates until templates exist (E4) |
| `accountPolicy.masking` | The standard Aadhaar template always masks; per-account policy comes with templates |
| Models | Not in the image: mounted at `/models`; `models.yaml` in the image pins their checksums |
| Not tested end to end | As agreed for today; integration tests against real services come with E6 |

## Entry 3: Driving licence, two-sided cards, digital PDFs (step E3, 2026-10-28)

**Goal:** the three gaps E2 left: no DL template, a card's back not read, and digital PDFs going through OCR.

### What we did

| Part | What | Where |
|---|---|---|
| **Driving licence template** | `standard-driving-licence`: number, name, DOB, issue date, validity (NT and TR), address, blood group, organ donor, vehicle classes. Recognised by "Driving Licence", "DL No", "Transport Department" or "Union of India" | `pipeline/domain/standard_templates.py` |
| **Two-sided cards** | When a card has a back, the AI fallback sees the front and back **stacked into one picture** (the web extraction app's `stack_sides`), so fields printed on the back (the Aadhaar address) can be read | `infrastructure/ai/vision_reader.py` |
| **Optional fields to AI** | Once the AI is asked (only when a field is unsettled: OCR first), optional fields OCR found empty go in the same request; an AI value still has to pass the field's rule | `application/services/extract_document.py` (`AiAssist`) |
| **Digital PDFs: no OCR** | A PDF with a text layer is read with PDFium (words with positions, grouped by the engine library's layout rules). Both readings are the text layer, so the rule alone decides; source `DIGITAL_TEXT`. Scans inside a PDF, and rotated pages, still go to OCR | `infrastructure/digital/pdf_text_reader.py` |

### Why it's built this way

| Choice | Why it matters |
|---|---|
| DL fields read from the whole page, anchored to captions | State formats differ and there are no labelled DL samples here, so measured regions would be guesses. Captions (`DL No`, `DOB`, `Valid Till`) are on every format. **Measure regions on real samples** before relying on OCR alone |
| Validity dates may be in the future | The date rule refuses dates after today by default; a licence's expiry is ahead (until 2100) |
| Stacking only for the AI | OCR templates measure regions on one side; stacking would move them. The AI reads both sides as one picture, which the web app benchmarked |
| Words split at PDFium's spaces | Glyph boxes are tight: narrow letters ("1", "r") leave gaps that looked like word breaks. Found by a test |

### Lessons

- **Two tests caught two real bugs before any document did:** words split inside ("Pe rmanent", "ABCPE1 234F") and a DL's future validity date rejected as "after today".

### Decisions and open questions

| Topic | Decision |
|---|---|
| DL accuracy | Caption-based OCR plus AI fallback. **Open:** a labelled DL set (state formats) to measure regions and accuracy (ML-12) |
| Two-sided OCR | OCR reads the front; the back is read by AI. **Open:** per-side templates (Aadhaar back) with E4 |
| Digital PDF layout | Text-layer positions are fractions of the unprepared page; a digital PDF isn't straightened, so they match |

## Entry 4: Templates and document types (step E4, 2026-10-28)

**Goal:** templates stop being code. They live in `extraction_db`, change through an API with versions and a publish
step, and the worker uses whatever is published.

### What we did

```
template/                         the new feature (IDP-03 ch. 2.3)
  domain/   models.py             Template (versions DRAFT -> PUBLISHED -> RETIRED, archive, clone), DocumentType
            definition_json.py    schema v1 JSON (IDP-03 ch. 2.5) <-> the pipeline's TemplateDefinition
            ports.py, exceptions.py
  application/services/           TemplateService, DocumentTypeService (async; the real permission checks)
  infrastructure/                 orm.py, repository.py: async SQLAlchemy, unit of work, idempotency store
  api/                            schemas, mappers, routes: /api/v1/templates, /api/v1/document-types
core/   security.py, permissions.py   bearer tokens (PyJWT, ADR 0005), roles -> permissions (IDP-00 ch. 7.2)
        db.py                     + the async engine for the API
pipeline/infrastructure/persistence/template_catalog.py   the worker reads published templates (cached 60 s)
seed.py                           python -m extraction.seed: the three standard templates and document types
migrations/0002_templates.py      document_types, templates, template_versions, template_test_runs, idempotency_record
```

### Rules

| Rule | Where |
|---|---|
| Only a DRAFT changes (409 `EXT-409-TEMPLATE-001`); one PUBLISHED per template (database index); restore brings back a RETIRED one | `Template`, migration 0002 |
| Publishing needs a passing test in the last 24 h, **or an override reason** (409 `EXT-409-TEMPLATE-002`). Test runs come with E6, so today every publish carries a reason, which is recorded | `Template.publish` |
| Standard templates: everyone reads, only `PLATFORM_ADMIN` changes. An account's own: only that account, anyone else gets 404 (SEC-05) | `TemplateService` |
| Non-idempotent writes need `Idempotency-Key`: same key returns the first result, same key with a different body is 409 | `TemplateService._once` |
| Saving a draft needs `If-Match` with the ETag; stale is 412 | repository `save` |
| A definition is checked completely: every problem comes back in `errors` (422 `EXT-422-DEFINITION-001`) | `definition_json` |

### Why it's built this way

| Choice | Why it matters |
|---|---|
| The JSON round-trips the standard templates exactly (tested) | A stored template reads the same as the code one; no behaviour change when templates move to the database |
| Unsupported schema parts are refused, not ignored (tables, CHECKSUM rules, page > 0, field types the pipeline can't read) | A template that saves but silently does nothing would be worse than a clear 422 |
| The worker falls back to the code templates when the database has none | Nothing breaks between deploying and running the seed |
| Async API, sync worker | RTR-04 for routes; the worker stays synchronous (CEL-01) |

### Lessons

- **Running the migrations on a real database caught a missing dependency:** SQLAlchemy's async mode needs `greenlet`
  (`sqlalchemy[asyncio]`); the API would have failed at start-up.
- **Rereading the spec caught a wrong code from E2:** IDP-03 names the time-limit error `EXT-504-PIPELINE-001`; fixed
  before anything used it.

### Decisions and open questions

| Topic | Decision |
|---|---|
| JWT library | **PyJWT** (MIT), not on the approved list: ADR 0005, **the lead's approval** |
| Document types per organisation | `account_id` added to `document_types` (IDP-03 ch. 2.4 has no column for it; IDP-00 says organisations have their own) |
| Categories | ID, FINANCE, TAX, LEGAL, HEALTH, EDUCATION, OTHER (IDP-03 says "ID/FINANCE/…"; confirm the list) |
| Publish without tests | Override reason required until E6 |
| Idempotency records | Kept; **open:** a cleanup job after 24 h |
| `POST /document-types` | No `Idempotency-Key`: a repeat is refused as a duplicate code (409), which is safe |

## Entry 5: The AI fallback goes through ai-gateway (2026-10-28)

**Goal:** IDP-03 ch. 4: only ai-gateway talks to the model server. ADR 0004's direct call is replaced (ADR 0006).

### What we did

| Change | Where |
|---|---|
| `GatewayFieldReader`: `field-read` batch form with page keys, field labels, account and priority | `pipeline/infrastructure/ai/gateway_reader.py` |
| Client-credentials service token, cached until 30 s before expiry, dropped on a 401 | `core/service_auth.py` |
| `AiCallContext` in, `AiReading` (values, model, prompt version) out | `pipeline/domain/ports.py` |
| Pages stored before the AI step, image keys added to the result after | `run_extraction.py` |
| `ExtractionRequest.priority` from the route it came on, stored with a default (no migration) | `jobs.py`, messaging and persistence mappers, `consumer.py` |
| A call with no answer is no longer counted in `usage.aiCalls` | `extract_document.py` |
| Settings `AI_GATEWAY__*` and `SERVICE_AUTH__*`; `AI_FALLBACK__*` only for `ENVIRONMENT=local` | `core/config.py`, `bootstrap.py` |

### Check it

```bash
cd services/extraction-service && make check
```

Result (`make check` on the dev machine, with Docker): 519 tests pass, 87% coverage.

## Entry 6: The rest of IDP-03 ch. 2: recheck, deep analysis, the designer, the bundle (step E6, 2026-10-28)

**Goal:** every job type and API IDP-00 and IDP-03 give extraction-service, so the service is complete. The open
contract points are in ADR 0007.

### What we did

| Piece | What it does | Where |
|---|---|---|
| Job kinds | EXTRACTION, RECHECK, DEEP_ANALYSIS share one item lifecycle and the same events | `jobs.py`, `item_lifecycle.py` |
| Recheck | `ExtractionRequested` + `options.recheck`: full pipeline on the original, the device's settled fields kept | `merging.merge_recheck`, `run_extraction.py` |
| Deep analysis | `DeepAnalysisRequested` -> `ai` queue -> ai-gateway `page-analysis` -> `result.analysis` (accepted fields untouched) | `run_deep_analysis.py`, `consumer.py` |
| AI classify | an unknown page goes to ai-gateway `classify`; ≥ 0.8 picks the template | `extract_document.AiAssist.classify` |
| AI field cap | more than 10 unsettled fields: review, no AI call | `AiAssist.assist` |
| Named template | a request's `templateId` is used without classifying | `ExtractDocumentCommand.template_forced` |
| Samples | `POST/GET .../samples`; a worker stores page 0 and engine A's words in `idp-templates` | `designer_service.py`, `designer_work.py` |
| Suggestions | `POST .../suggestions`: the prepared sample to ai-gateway `template-suggest` | `designer_adapters.py` |
| Test runs | `POST/GET .../test-runs`: every ready sample read with the tested version, scored per field | `evaluation.py`, `designer_work.py` |
| Publish gate | a passing run (same definition, < 24 h, required fields ≥ 90 %) or a reason | `TemplateService.publish` |
| Mobile bundle | `GET /api/v1/templates/bundle`: published definitions, rule ids, thresholds; ETag / `since` -> 304 | `DesignerService.bundle` |
| Account deletion | `AccountDeleted`: templates archived, types off, samples deleted | `consumers.py`, `TemplateService.archive_account` |
| `pipeline_runs` | stage timings, decision counts, AI calls per finished item | `runs.py`, `repository.py` |
| Metrics | IDP-03 ch. 2.12 counters and histograms; `/metrics` on the API, multiprocess in the workers | `metrics.py`, `worker.py`, `main.py` |
| Migration 0003 | `template_samples`, `pipeline_runs`, test-run columns | `migrations/versions/0003_designer_and_runs.py` |
| Integration tests | the designer on Postgres; a request through RabbitMQ, the worker, MinIO and the outbox | `tests/integration/` |

### Rules

| Rule | Where |
|---|---|
| SEC-05: keys in messages and sample files must be the account's own | `mappers._own`, `designer.check_sample_files` |
| TX-03: tasks are sent after the commit | `DesignerService` |
| CEL-03: every task checks its row first, so a repeat does nothing | `pending_sample`, `start_test_run`, `ItemLifecycle.start` |
| ML-07: the new thresholds are settings | `AI_GATEWAY__MAX_FIELDS_PER_DOCUMENT`, `__CLASSIFY_MIN_CONFIDENCE`, `TEMPLATES__PUBLISH_MIN_ACCURACY` |
| MIG-02, MIG-05: written by hand, additive only | migration 0003 |
| DEP-02: `prometheus-client`, `celery`, `boto3` move to the main list (all approved) | `pyproject.toml` |

### Check it

```bash
cd services/extraction-service
uv sync --all-extras
make check                          # includes the integration tests: needs Docker
uv run alembic upgrade head         # against a real extraction_db, before deploying
```

Result (`make check` on the dev machine, with Docker): 591 tests pass (585 unit, 6 integration), 91.6% coverage. The
full-stack test uses the same RabbitMQ and MinIO containers as conversion-service: the stock testcontainers RabbitMQ
(3.13) wasn't ready in time on this machine.

## Entry 7: Word, Excel and PowerPoint input (2026-10-28)

**Goal:** IDP-03 ch. 2.6 stage 5 for Office documents: exact text, no OCR.

### What we did

`LibreOfficePdfConverter` (`pipeline/infrastructure/digital/office_to_pdf.py`) turns `doc/docx/odt/xls/xlsx/ods/
ppt/pptx/odp` (by content) into a PDF, which the digital-PDF path reads with positions. Used by the extraction run and
the designer's samples. The worker image gets LibreOffice and the Noto fonts; `ExtractionRequested` accepts the Office
content types. Why not python-docx/openpyxl: their text has no positions to match template boxes (ADR 0008).

### Check it

```bash
cd services/extraction-service && make check
```

Result (`make check` on the dev machine, with Docker): 598 tests pass (592 unit, 6 integration), 92.9% coverage; one
converts a hand-made Word file with the real LibreOffice and reads its text back.

## Entry 8: The synchronous extract and the AI step on field crops (2026-11-03)

**Goal:** the backend's direct HTTP call for one document, answered on the same request, read the way the team
planned for both paths: two OCR engines first, and only the fields they disagree on or are unsure of cut out and sent
to the AI. A document no template knows goes to the AI whole. Decisions: ADR 0009 (and ai-gateway ADR 0003).

### What we did

- **The AI step reads crops** (`AiAssist.assist_with_crops`, `pipeline/domain/crops.py`, `GatewayCropReader`): each
  field still NEEDS_REVIEW is cut out of the page (its box, moved with its anchor, plus a margin), stored under
  `crops/`, and read alone through ai-gateway's `field-read` single form with both engines' readings as hints; up to
  3 at once. `AI_GATEWAY__READ_MODE=page` keeps the whole-page request to compare. Both paths use it.
- **The `extraction-sync` process** (`sync_main.py`, worker image, port 8003) serves `POST /api/v1/extract`: the
  OCR engines load once; each request runs the pipeline in a worker thread, two at once, one more answers 429.
- **The `quick/` feature**: the file checks; the use case (pipeline, then `generic-read` when no template matched;
  the workspace folder deleted whatever happened); the workspace, the pipeline runner (INTERACTIVE lane, the crops
  folder, PDFs over 2 pages refused) and the sync ai-gateway client; the route. The slim API no longer has it.
- Permission `document:write` for every role; the plan for the daily AI allowance from the token's roles.
- **`POST /api/v1/extract/generic`**, straight to the model: the pages prepared (no OCR, no quality gate) and read
  whole by ai-gateway's `generic-read`; same inputs, limits and answer. The stack check has a twelfth check for it.

New error codes `EXT-413-FILE-001`, `EXT-415-FILE-001`, `EXT-429-BUSY-001`, `EXT-429-AI-001`; new settings
`QUICK__*`, `AI_GATEWAY__READ_MODE`, `AI_GATEWAY__CROP_PADDING`, `AI_GATEWAY__CROP_CONCURRENCY`; new dependency
`python-multipart`; two new import contracts. The local stack runs `extraction-sync`, and `make check` has two new
checks: the card through `POST /api/v1/extract` and `POST /api/v1/extract/generic` with the user's token.

### Check it

```bash
cd services/extraction-service && make check
cd services/local-stack && make prepare && make up && make check
```

Result here (no Docker): ruff, mypy and the 10 import contracts clean; 649 unit tests pass, 89% coverage, the new code
97–100%. The database integration tests and the stack check need Docker. Until the real model server is in, the mock
model answers the crops (with null: the fields stay for review) and the generic read (a made-up PAN).

## Entry 9: No lighting checks on PDF pages (2026-11-03)

**Goal:** the backend's clear Aadhaar PDFs were all refused with `EXT-422-QUALITY-003` (glare): the white A4 paper
measured as 68% glare. Decisions: ADR 0010.

### What we did

`list_quality_issues(..., photographed=False)` skips brightness and glare; `PrepareDocumentService` passes it for a
PDF (Office files arrive as one). New setting `QUALITY__LIGHTING_REJECTS` (default true): false notes a photo's
lighting instead of refusing it; the local stack sets false.

### Check it

The backend's PDF through the real quality gate: refused before (glare 0.68), prepared now. Unit tests for both.

## Entry 10: A PDF's text layer is used only when it shows which document it is (2026-11-03)

**Goal:** the backend's Aadhaar PDF (a scanned card on A4, plus a one-line text header) took 34 s and every field came
from the AI. Its text layer held 10 words (over the 20-character "digital PDF" mark), so OCR was skipped; those words
named no template, so the AI classified the page and read all four fields from crops.

### What we did

`ExtractDocumentService._read`: the text layer replaces OCR only when its keywords match a template (or the request
named the template); otherwise both OCR engines read the page. A real e-PAN or e-Aadhaar keeps its exact text.

### Check it

A unit test with a header-only text layer. The same PDF locally: read by OCR, classified Aadhaar by keywords, number,
name, date of birth and gender settled by OCR (no AI), in 7.8 s; before, an AI classify and four AI crops.

## Entry 11: Extraction only, at speed (2026-11-04)

**Goal:** a good card photo answered in 1 to 2 seconds, with nothing but extraction in the way. Decisions: ADR 0011.

### What we did

- `QUALITY__REJECTS` (default false) replaces `QUALITY__LIGHTING_REJECTS`: the gate measures and records, and refuses
  nothing. Two good 860 px card photos had been refused as "low resolution" (the gate wanted 900 px).
- `QUICK__REQUIRE_TOKEN` / `QUICK__DEFAULT_ACCOUNT_ID` / `QUICK__DEFAULT_PLAN`: the local stack lets requests without a
  token into extraction-sync on the test account; a sent token is still checked.
- `OCR__THREADS` default 2 (was 4): the two engines read at once and fought over 8 cores.

### Check it

The two good test photos (PAN and Aadhaar, made available by the lead), through the pipeline, no AI: with 4 threads
1.96 to 2.68 s; the engines alone 2.2 s together with 4 threads, 1.2 s with 2. PAN: 4 of 4 fields by OCR. Aadhaar:
5 by OCR; the address was read by engine A only (B read nothing), so it still goes to the AI.

## Entry 12: No storage on the synchronous lane (2026-11-04)

**Goal:** the synchronous lane is HTTP in, OCR, the AI only if needed, HTTP out. It had been storing every request's
pages in the backend's MinIO, and failed with 500 when that machine didn't answer. Decisions: ADR 0012 (and
ai-gateway ADR 0004).

### What we did

`InlineImages` holds a request's pages and crops in memory; `AiGatewayClient.image()` sends them inside the call as
`{"data": ...}`; `MemoryWorkspace` drops them when the request ends; `build_quick_extract` needs no storage. WebP is
accepted on the synchronous routes.

### Check it

The two good test photos through the whole synchronous lane, with no storage configured: PAN 1.15 to 1.33 s, all four
fields by OCR; Aadhaar 1.42 to 1.82 s, six by OCR (the address, read by one engine only, goes to the AI).

## Entry 13: PDF pages capped at 2000 px (2026-11-04)

**Goal:** the same card took 1.3 s as a photo and 6 to 7 s as a PDF. Rendered at 300 DPI, the PDF page was 2688 px
wide (4.5 MP, nine times the photo): OCR took 3 s and left a field unsure, so the AI was asked.

### What we did

`LIMITS__PDF_MAX_SIDE_PX` (default 2000) caps a rendered page's long side, in the page loader and the text-layer reader.

### Check it

The PAN photo saved as a PDF: 2.8 s, all four fields by OCR (before: 3.3 s and one AI call). A test for the cap. Still
open: a card on an A4 page is not cut out of it, so OCR finds none of its fields there.

## Entry 14: A photo saved as a PDF is read from the photo (2026-11-04)

**Goal:** the backend's Aadhaar PDF took 11 s: one picture (768 x 972 px) covering 80% of a letter page was rendered as
the whole page at 300 DPI (2550 x 3301 px, eleven times the pixels, with margins), so OCR was slow and missed fields.

### What we did

`pdf_pictures.picture_of`: a page whose largest image covers `LIMITS__PDF_PICTURE_MIN_FRACTION` (0.3) of it, placed
upright, is a picture page. The page loader takes that picture's own pixels; the text-layer reader skips the page.

### Check it

A made-up card as a picture PDF: 1.65 s and the same fields as the photo itself (1.76 s). The backend's PDF: OCR on
768 x 972 px in 3.1 s (before about 8 s), six of eight fields by OCR, the address among them; the two Hindi fields still
go to the AI. Three tests (picture page, small image rendered as before, no text layer on a picture page).

## Entry 15: English only (2026-11-04)

**Goal:** the product extracts English only for now.

### What we did

- The standard Aadhaar template is version 2, without `name_hi` and `address_hi` (PAN and DL had no Hindi fields). The
  field-language mechanism stays for organisations' own templates; its tests use Hindi fields of their own
  (`tests/support/hindi_fields.py`).
- `seed` publishes a newer shipped version of a standard template and retires the old one, but only while every stored
  version is the seed's own (an admin's change is never overwritten); a fresh database stores the shipped version
  number. Two integration tests.
- Engine B keeps reading Devanagari, though no Hindi is extracted: Aadhaar lines mix the scripts, and English-only B,
  tried on the backend's bilingual PDF, lost the date of birth and read gender and PIN code too faintly (3 of 6 fields
  by OCR, against 6 of 6 with it).

### Check it

PAN photo 1.16 s, all four fields by OCR. Aadhaar photo 1.44 s, five of six (engine B reads no address on it, so the
address goes to the AI). The backend's Aadhaar PDF 3.3 s, six of six by OCR, no AI call (it took 11 s with the Hindi
fields).

## Entry 16: Custom templates, keys in the request (2026-11-04)

**Goal:** users' own templates (a green card, say): the backend sends the keys with each request, we read exactly
those, OCR first, and store nothing. Decisions: ADR 0013 (option B; no shared database, DB-01).

### What we did

- `FieldDefinition.value_after`: a field defined by its label only; the value is the text after the label on its
  line, or the line below; nothing when the label isn't on the page. A label must open its piece of text.
- `pipeline/domain/custom_template.py`: keys (`key`, `label`, `type` text/date/number) to a one-request template;
  dates may lie ahead (an expiry date). The AI's ten-field limit doesn't apply to a custom template.
- `POST /api/v1/extract/custom` on extraction-sync (`fields`: a JSON list, 1 to 50 keys, checked: 400 otherwise). The
  stack check has a thirteenth check for it.

### Check it

A made-up PAN card with four custom keys, every label printed: 1.68 s, all four by OCR. With a fifth key whose label
isn't printed: 5.4 s, the four by OCR and the fifth by the AI. Two flaws found and fixed on the way: a label matched at
the end of a title ("Department" in "INCOME TAX DEPARTMENT"), and an apostrophe stopped "Father's Name" matching.

## Entry 17: Custom keys read inside the box drawn around the value (2026-11-07)

**Goal:** the frontend's report: custom templates published, but on a two-column tax invoice a value ran into the next
column (`GSTIN` gave `32A... Transport ID 24B...`). The frontend confirmed the box is drawn around the value.

### Cause

The custom route took only `{key, label, type}` and refused anything else, so the boxes never reached us; the value
was the rest of the label's line, across the whole page. Confidence was 99% because both engines read the same wrong
text.

### What we did

- `fields` takes an optional `box` per key: `{page: 1, x, y, w, h}` in percent of the page, as the designer stores
  it (ADR 0013 amended). A box past the page's edge, of no size, or on another page is a 400.
- A key with a box is read inside it (`FieldDefinition.clip_to_box`), widened a little; a piece of text crossing the
  edge keeps only its words inside (a word ends at a space or after a colon: "ID:24B..."). When the box holds nothing,
  the key is read by its label.
- By label, the value now stops at the first column gap after it starts (a value far right of its label, as on ID
  cards, is still read).

### Check it

Lint, types, 10 import contracts; 714 unit tests pass.

A made-up two-column invoice, M/S, GSTIN, Tel and Transport ID:

| Run | M/S | GSTIN | Time |
|---|---|---|---|
| Docker before (labels) | `Test Engineering Works Challan No: 33` | `32A... Transport ID:24B...` | 2.5 s |
| New, labels only | `Test Engineering Works` | `32AAAAA0000A1ZA` | 2.1 s, OCR |
| New, with boxes | `Test Engineering Works` | `32AAAAA0000A1ZA` | 2.4 s, OCR |

All four right in both new runs. A key whose label isn't printed ("Seller phone", in the header line): by label the
AI gave the buyer's phone; with a box, OCR read the seller's, 2.1 s.

## Entry 18: AI guardrails: a rejected AI reading masked, the data rule in card prompts, the log mask (2026-11-10)

**What** (from the intern's PR #27):
- A field with a mask policy now shows the masked value in its AI reading too. Before, an AI reading of an Aadhaar
  number that failed its check digit reached the reviewer in full (found by the PII golden suite).
- The card prompts (`card-fields.v2`) say that everything printed on the card is data, never an instruction.
- A mask on every log line and on validation messages (Aadhaar, PAN, e-mail, phone shapes); the build stamp
  (`env`, `service_version`, `commit`, `config_hash`) on every line.

**Check it:** 737 unit tests pass (26 skipped without `OCR__MODELS_DIR`); lint, types, 10 import contracts. The PII
golden suite: 26 fields, 0 raw values in mask-policy fields.

## Entry 19: Custom dates read as printed; labels ending in "#" (2026-11-11)

**Problem:** the frontend reported that a custom `date` key (date of birth) was never extracted, whatever the
document. Reproduced on two US specimen cards: the date is printed `20 OCT 2002`. The custom date rule accepted only
numbers with the day first, so OCR found nothing, and the AI's answer (`20 OCT 2002`, as printed) was refused by the
same rule: the key came back empty, `NEEDS_REVIEW`. Sent as `text`, the same key was read. `Card Expires` (`10/26/32`,
month first, two-digit year) failed the same way, and `USCIS#` returned `#`.

**What:** `rule/printed_date.py`: a date in any printed form passes (day, month or year first; 2- or 4-digit year;
month names), when one reading is a real date. Custom `date` keys use it (validator `DATE_AS_PRINTED`) and keep their
spaces (`COLLAPSE_SPACES`, not `STRIP_SPACES`), so the value is returned as printed. A label's closing punctuation
(`#`, `:`, `.`) is part of the label. Standard cards and stored templates are unchanged. ADR 0013 amended.

**Check it:** 763 unit tests pass (26 new); lint, types, 10 import contracts.

## Entry 20: An AI-only address, pincode or name on an Aadhaar card goes to review (2026-11-11)

**Problem:** the intern's Aadhaar accuracy set (100 made-up cards, `services/golden-tests/accuracy/aadhaar/`) found
58 of 588 fields wrong and `ACCEPTED`, and a review rate of 0%. 47 of the 58 were values only the AI read (OCR could
not settle the field): addresses (25), pincodes (17) and names (4), on blurred, glared and cropped cards. Their rules
check only the shape (any text is an address, any six digits a pincode), so a wrong guess passed and was accepted.

**What:** `FieldDefinition.ai_needs_review`. When it is set, a value only the AI read keeps `NEEDS_REVIEW` instead of
`ACCEPTED`; the value and `source: AI` are unchanged. Set on the Aadhaar card's name, pincode and address, and the Aadhaar
template goes to version 3: the service reads templates from the database, and the seed (`extraction-migrate`)
publishes a shipped template only when its version is newer. The seed now numbers it with the shipped version, as its
docstring said. Template JSON carries the flag as the optional `aiNeedsReview` (written only when true). No new
response field, no extra AI call; results show `templateVersion` 3 for Aadhaar.

**Not fixed here:** the 11 wrong values both OCR engines agreed on (9 addresses, 2 numbers).

**Check it:** 767 unit tests pass (4 new); lint, types, import contracts. The accuracy set before and after: see the
PR.

## Entry 21: One picture with a card's front and back becomes two pages (2026-11-11)

**Problem:** the frontend sent one picture with a national ID card's front above its back. Before reading, the
service cuts the largest card-shaped area out of a photo (`card_detector.detect_card`), so it kept the back and
dropped the front. OCR then found no front labels, and the AI, given only the back, read the machine-readable lines
(`I<JAMA...<<<<`): the ID number, names and nationality came back as MRZ text. On the whole picture OCR read every
front field right.

**What:** `card_detector.find_two_cards`: two separate card-shaped areas of about the same size
(`PREPARE__TWO_CARDS_MIN_SIZE_RATIO`, 0.7; 0 turns it off) are cut apart, with a margin, top one first (left one
when side by side). The page loader turns such a picture, or a scan inside a PDF, into two pages, exactly as if the
front and back had been sent as `file` and `back`. One card, three or more cards, and rendered PDF pages are left as
they were. The API already takes `back`; the frontend will send two files later.

**Known limit:** OCR reads the first page only; the second side goes to the AI. A picture with the back on top gets
its front fields from the AI.

**Check it:** 774 unit tests pass (7 new); lint, types, import contracts.
