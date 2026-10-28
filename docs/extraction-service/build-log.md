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
