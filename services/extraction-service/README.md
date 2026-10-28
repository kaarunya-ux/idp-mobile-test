# extraction-service

Reads documents into structured fields: standard and organisation templates, the extraction pipeline (quality gate, preparation, dual OCR, verification, AI fallback), re-checks of on-device results and the evaluation harness for OCR engines. Specified in IDP-03 ch. 2 and built to the Python Engineering Standard. Owned by the Python team (IDP-00 ch. 3.1).

**Status: it runs as a service (build steps E1 and E2, 2026-10-28; see the [build log](../../docs/extraction-service/build-log.md)).** Java's `ExtractionRequested` arrives over RabbitMQ; a Celery worker reads the file from MinIO and runs the whole pipeline (SCAN, QUALITY_CHECK, PREPARE, CLASSIFY, READ with engines A and B, VERIFY, AI_ASSIST, ASSEMBLE); the page images and `result.json` go to `idp-derived`, and `JobProgressed` / `ExtractionCompleted` / `WorkFailed` go back on `idp.events` through an outbox in `extraction_db`. Standard templates: PAN, Aadhaar and Indian driving licence. Digital PDFs are read from their text layer, with no OCR; a card's back is read together with its front by the AI fallback. **OCR is the first layer; AI is the fallback** for fields the engines can't settle, accepted only when the field's rule passes (ported from the web extraction app; ADR 0004). **Templates and document types** are stored in `extraction_db` and managed through the API below; the worker uses the published ones (standard plus the account's own). Suggestions, test runs, recheck, deep analysis and the mobile bundle come next (build log, steps E5-E6). The old prototype is in [`ocr_test_app/`](ocr_test_app/README.md).

## Processes

| Process | Command | Image (`docker build --target`) |
|---|---|---|
| API (templates later; probes now) | `uvicorn extraction.main:app --port 8000` | `api` |
| `intake` (FastStream consumer) | `faststream run extraction.consumers:create_app --factory` | `service` |
| `relay` (outbox publisher) | `python -m extraction.outbox_relay` | `service` |
| migrations (pre-deploy job) | `alembic upgrade head` | `service` |
| standard templates (after migrating, safe to repeat) | `python -m extraction.seed` | `service` |
| `worker-pipeline` (Celery) | `celery -A extraction.worker worker -Q pipeline -c 4 --prefetch-multiplier 1` | `worker` (mount the models at `/models`) |

## Run it

```bash
uv sync                # create .venv from uv.lock, with the dev tools
make check             # format, lint, types, architecture rules, tests: exactly what CI runs
make run               # API on http://127.0.0.1:8000, probes at /health/live and /health/ready
```

`make audit` also runs `pip-audit` and `gitleaks`, which must be installed separately.

## Environment variables

Read once at startup by `extraction.core.config.Settings`; an invalid value stops the process. Nested groups use `__`. For local runs, copy `.env.example` to `.env`.

| Variable | Default | Allowed values | Purpose |
|---|---|---|---|
| `SERVICE_NAME` | `extraction-service` | 1-64 characters | Name in logs and traces |
| `ENVIRONMENT` | `local` | `local`, `dev`, `test`, `staging`, `prod` | Where the process runs |
| `LOGGING__LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` | Lowest level written |
| `LOGGING__FORMAT` | `json` | `json`, `console` | `console` for readable local logs; JSON everywhere else |
| `OCR__MODELS_DIR` | `models` | a directory | Where the OCR model files are; every path in `models.yaml` is relative to it (workers only) |
| `OCR__MODELS_FILE` | `models.yaml` | a file | The model registry (ML-01) |
| `OCR__THREADS` | `4` | 1-64 | Intra-op CPU threads per model session. A and B run at the same time, so on 8 cores 2 was fastest in a test |
| `OCR__INFERENCE_TIMEOUT` | `PT60S` | 1 s - 10 min (seconds or ISO 8601) | A model run past this is stopped (ML-06) |
| `OCR__MAX_PIXELS` | `60000000` | 1M - 1G | Pages with more pixels are refused (ML-06) |
| `QUALITY__BLUR_MIN` | `100.0` | > 0 | Variance of the Laplacian below this is blurred |
| `QUALITY__BRIGHTNESS_RANGE` | `[0.25,0.9]` | JSON list of two fractions | Mean luminance outside this is too dark / too bright |
| `QUALITY__GLARE_MAX` | `0.04` | 0 - 1 | Share of the page covered by glare |
| `QUALITY__MIN_SHORT_SIDE_PX` | `900` | >= 1 | A shorter document side is too low resolution |
| `QUALITY__SATURATION_LEVEL` | `250` | 200 - 255 | Grey level from which a pixel counts as saturated |
| `QUALITY__GLARE_SOFT_EDGE_GRADIENT` | `15.0` | > 0 | Grey levels per pixel: softer-edged saturated areas are glare |
| `QUALITY__REQUIRE_DOCUMENT_EDGES` | `false` | true / false | Refuse a page whose document edges cannot be found |
| `LIMITS__MAX_FILE_SIZE` | `200MB` | a size | Larger files are refused |
| `LIMITS__MAX_PAGES` | `200` | 1 - 10000 | Files with more pages are refused (`EXT-422-PAGES-001`) |
| `LIMITS__MAX_IMAGE_PIXELS` | `180000000` | 1M - 1G | Images with more pixels are refused; PDF pages are scaled down to fit |
| `LIMITS__PDF_DPI` | `300` | 72 - 600 | Resolution PDF pages are rendered at |
| `PREPARE__ORIENTATION_MIN_CONFIDENCE` | `0.6` | 0 - 1 | Below this the page is not turned |
| `PREPARE__MIN_CARD_AREA_FRACTION` | `0.15` | 0 - 1 | A card must cover at least this much of the photo |
| `VERIFY__NAME_SIMILARITY` | `0.95` | 0 - 1 | Default threshold for SIMILARITY fields (IDP-03 ch. 2.7) |
| `REVIEW__CONFIDENCE_THRESHOLD` | `0.85` | 0 - 1 | Below this an accepted field still needs review |

| `DB__URL` | — (required by intake, relay, workers) | `postgresql+psycopg://user:password@host:5432/extraction_db` | Inbox, outbox and job items |
| `DB__POOL_SIZE` / `DB__MAX_OVERFLOW` | `5` / `2` | 1–50 / 0–50 | Connections per process |
| `DB__STATEMENT_TIMEOUT` / `DB__LOCK_TIMEOUT` / `DB__IDLE_IN_TRANSACTION_TIMEOUT` | `5` / `3` / `10` s | | PostgreSQL stops anything slower (DB-02) |
| `RABBIT__URL` | — (required by intake, relay) | `amqp://user:password@host:5672/vhost` | Where `ExtractionRequested` arrives and events are published |
| `RABBIT__PREFETCH` / `RABBIT__DELIVERY_LIMIT` | `10` / `5` | | Messages held at once / deliveries before the dead-letter queue |
| `CELERY__BROKER_URL` | — (required by intake, workers) | `amqp://...` | The task queue between intake and the pipeline workers |
| `CELERY__SOFT_TIME_LIMIT` / `CELERY__TIME_LIMIT` | `540` / `600` s | 30 s – 1 h | Soft: the item fails cleanly (`EXT-504-PIPELINE-001`); hard: the task is killed |
| `CELERY__MAX_RETRIES` | `3` | 0–10 | Retries for passing failures (database, storage) before `EXT-500-INTERNAL-001` |
| `STORAGE__ENDPOINT` / `STORAGE__ACCESS_KEY` / `STORAGE__SECRET_KEY` | — (required by workers) | | MinIO, run by the Java team |
| `STORAGE__BUCKET_DERIVED` | `idp-derived` | a bucket name | Page images and `result.json` (IDP-00 ch. 7.7) |
| `STORAGE__MAX_INPUT_SIZE` / `STORAGE__TIMEOUT` | `200MiB` / `30` s | | Larger inputs are refused before downloading |
| `OUTBOX__BATCH_SIZE` / `OUTBOX__POLL_INTERVAL` / `OUTBOX__LEASE` / `OUTBOX__MAX_ATTEMPTS` / `OUTBOX__BACKOFF_MAX` | `100` / `1` s / `60` s / `10` / `300` s | | The relay (as in conversion-service) |
| `AUTH__ISSUERS` | `[]` (every protected route 401) | JSON list of `{"issuer": ..., "jwks_url": ...}` | Trusted token issuers (Keycloak; IDP-01) |
| `AUTH__AUDIENCE` / `AUTH__JWKS_CACHE` / `AUTH__LEEWAY` | `idp-api` / `600` s / `30` s | | Required audience; how long keys are cached; allowed clock skew |
| `AI_FALLBACK__URL` | — (off) | `http://10.0.3.66:8081` | The model host until ai-gateway exists; unset = unsettled fields stay NEEDS_REVIEW |
| `AI_FALLBACK__API_KEY` / `AI_FALLBACK__MODEL` | — | | Must match the model host; the model id is sent with requests |
| `AI_FALLBACK__TIMEOUT` / `AI_FALLBACK__MAX_EDGE_PX` | `120` s / `1280` | | Give up after this; longest image side sent |
| `AI_FALLBACK__CONFIDENCE` | `0.9` | 0–1 | Given to an AI value that passes its rule (ML-07: change with an evaluation) |

## Dependencies

None at runtime yet. PostgreSQL, Redis, RabbitMQ, MinIO and the AI gateway arrive with the features that use them (IDP-03 ch. 2.2, 2.4, 2.11).

## API

Through the Java gateway, with a Keycloak bearer token (SEC-01: JWKS, `iss`, `aud=idp-api`, `exp`; ADR 0005). The
account always comes from the token's `account_id` (SEC-05): another account's template answers 404. Roles become
permissions as IDP-00 ch. 7.2 says (`ORG_ADMIN`/`ORG_OWNER` write and publish; every role reads; `PLATFORM_ADMIN`
manages standard templates). Errors are RFC 9457 problem details.

| Method | Path | Permission | Notes |
|---|---|---|---|
| `GET` | `/api/v1/document-types` | `template:read` | standard + the account's own, active only |
| `POST` / `PATCH` | `/api/v1/document-types[/{code}]` | `template:write` | `scope: STANDARD` needs `PLATFORM_ADMIN` |
| `GET` | `/api/v1/templates` | `template:read` | `scope`, `documentType`, `q`, `state`, `cursor`, `limit` (≤ 200) |
| `POST` | `/api/v1/templates` | `template:write` | `Idempotency-Key`; 201 + `Location` + `ETag` |
| `GET` | `/api/v1/templates/{id}` · `/versions/{n}` | `template:read` | with `ETag` |
| `PUT` | `/api/v1/templates/{id}/versions/{n}` | `template:write` | DRAFT only (409); `If-Match` (412 when stale) |
| `POST` | `/api/v1/templates/{id}/versions` | `template:write` | new draft from the published version |
| `POST` | `/api/v1/templates/{id}/versions/{n}/publish` · `/restore` | `template:publish` | publish needs `overrideReason` until test runs exist |
| `POST` | `/api/v1/templates/{id}/clone` · `/api/v1/templates/import` | `template:write` | into the caller's account |
| `GET` | `/api/v1/templates/{id}/export` | `template:write` | portable JSON |
| `DELETE` | `/api/v1/templates/{id}` | `template:write` | archives; never deletes |

Not yet: suggestions (needs ai-gateway, E5), samples, test runs and the mobile bundle (E6).

## Layout

```
src/extraction/
  main.py          create_app(): middleware, error handlers, routers, lifespan
  health.py        probe router
  core/            config.py, logging.py (+ correlation id), errors.py (DomainError), problem_details.py (RFC 9457
                   handlers), error_codes.py, clock.py, ids.py
  rule/            pure field rules, no framework and no I/O: pan.py, aadhaar.py, dates.py, checksum.py (Verhoeff),
                   normalizers.py, outcome.py (RuleOutcome), exceptions.py
src/idp_ocr_engines/  the OCR engine library (IDP-03 ch. 1, `idp-ocr-engines`); imports nothing from `extraction`
  model.py         Box (page fractions), PageImage, OcrWord, OcrPage, OcrRegion
  port.py          OcrEnginePort: engine_id, engine_version, warm_up, read_page, read_region
  registry.py      models.yaml reader; verifies every model file's SHA-256
  session.py       CPU ONNX sessions and runs with a timeout
  layout.py        line and block ids; ctc.py shared decoding; limits.py caps; languages.py
  engines/         db_detector.py, paddle_recognizer.py, ppocr.py (PP-OCRv5 mobile, v6 small, Devanagari)
models.yaml        the model registry (ML-01)
docs/adr/          architecture decision records
tests/unit/        mirrors src/
ocr_test_app/      the flat prototype being migrated, one step at a time
```

The target tree for the whole service is IDP-03 ch. 2.3.

## The rules (`rule/`)

Each rule takes a string and returns a `RuleOutcome(rule_id, passed, message)`, the `rule` object of the extraction result (IDP-00 ch. 7.8). A failed rule is normal flow for OCR output, so nothing is raised; the message describes what was expected and never quotes the value. Rules read no clock and no settings: "today" is passed in (DOM-05).

| Function | Rule id | Checks |
|---|---|---|
| `evaluate_pan_number` | `pan-format` | five letters, four digits, a letter; holder-type letter in position four |
| `evaluate_aadhaar_number` | `aadhaar-verhoeff` | twelve digits not starting with 0 or 1 and a valid Verhoeff check digit, or the masked form `XXXX XXXX 1234` |
| `evaluate_date` | caller's id | an accepted format (`dd/MM/yyyy`, `ddMMyyyy`, `yyyy`, ...), a real calendar date, inside the given range |
| `normalize_value` | | the `normalize` operations of IDP-03 ch. 2.5 (`UPPER` ... `OCR_CONFUSABLES_ALNUM`), applied in order |

Two choices carried over from the prototype are flagged for confirmation: the PAN holder-type list is stricter than the pattern shown in IDP-03 ch. 2.5, and `repair_slash_misread` (OCR reading `/` as `1`) has no normalizer in IDP-03. The OCR look-alike tables hold only the pairs IDP-03 names (O, I/l, S, B); the prototype also mapped D, Q, L, Z and G, which waits for a benchmark (ML-12). GSTIN, IFSC, Luhn and amount rules are added with the standard templates that need them.

## The first pipeline stages (`pipeline/`, EXT-006)

`PrepareDocumentService` runs SCAN, QUALITY_CHECK and PREPARE for one file (the `prepare` task of IDP-03 ch. 2.6). The page loader checks the
real file type from the content, caps size, page count and pixels, applies EXIF rotation and renders PDFs at 300 DPI. The quality gate measures
blur, brightness, glare, resolution and document edges on the document itself and refuses a page with the matching `EXT-422-QUALITY-00x`
error. PREPARE cuts the card out of a photo, removes tilt (Hough lines), turns the page upright with the `doc-orientation` model, reduces noise
gently, evens contrast (CLAHE) and makes a black-and-white copy. Every step is deterministic.

Where this deliberately differs from IDP-03, with the reason (all flagged for confirmation; numbers are from our 19 test images):

- **Glare.** A plain saturated-pixel share rated every white Aadhaar card as 67-74% glare. Here only saturated areas with a soft edge count; sharp
  ink or paper edges do not. Tune with `QUALITY__SATURATION_LEVEL` and `QUALITY__GLARE_SOFT_EDGE_GRADIENT`, against real glare photos.
- **Document edges.** IDP-03 stage 2 rejects a page whose edges are not found, while stage 3 continues when the warp fails. 15 of 19 images are
  crops or scans with no detectable edges, so the rejection (`-005`) is off unless `QUALITY__REQUIRE_DOCUMENT_EDGES=true`.
- **"Too bright"** has no code in IDP-03 and uses the glare code `-003`.
- **Defaults vs the test images.** At IDP-03's default minimum of 900 px on the short side, 18 of the 19 images would be refused as too low
  resolution. They are low-resolution web images; the golden set must be made of real phone photos.

## The OCR engine library (`idp_ocr_engines`)

`idp_ocr_engines` is the library IDP-03 ch. 1 calls `idp-ocr-engines`: adapters for the OCR engines behind one protocol,
shared with conversion-service. It lives in this project until the `idp-py-platform` repository exists, and an
architecture rule keeps it from importing anything of the service so it can be lifted out unchanged.

```python
from pathlib import Path
from idp_ocr_engines import ModelRegistry, PageImage, RuntimeLimits
from idp_ocr_engines.engines.ppocr import PpOcrEngine, PpOcrModels

engine = PpOcrEngine.from_registry(
    engine_id="ocr-a",
    registry=ModelRegistry.from_file(Path("models.yaml")),
    models_dir=Path("/srv/models"),
    models=PpOcrModels(
        detector="ppocrv5-mobile-det",
        recognizer="ppocrv5-mobile-rec",
        extra_recognizers={"hi": "ppocrv5-devanagari-rec"},  # read only when Hindi is asked for
    ),
    limits=RuntimeLimits(threads=4, inference_timeout_seconds=60, max_pixels=60_000_000),
)
engine.warm_up()  # once per worker process (ML-03)
page = engine.read_page(PageImage(width=w, height=h, rgb=pixels), ["en", "hi"])
region = engine.read_region(image, Box(0.05, 0.3, 0.6, 0.07), ["en"])  # re-read one field
```

- **Result.** `OcrPage` holds words in reading order, each with a page-fraction `Box`, a 0-1 confidence, and line and block ids. Confidence is
  the geometric mean of the recogniser's per-character probabilities: a ranking signal, not a calibrated probability.
- **Registry.** Engines name models by registry name; `models.yaml` records each file's SHA-256 and the engine refuses to start on a mismatch
  (ML-01, ML-02). Nothing is downloaded at run time. `OCR__MODELS_DIR` points at the folder holding the files.
- **Languages.** An engine raises `UnsupportedLanguageError` for a language it cannot read; it never reads something else (ML-10). English is
  read by the general recogniser; Hindi needs the Devanagari recogniser.
- **Limits.** Pages over `OCR__MAX_PIXELS` are refused and a model run past `OCR__INFERENCE_TIMEOUT` is stopped (ML-06). Everything runs on the
  CPU provider; there is no silent fallback.
- **Tests.** Logic is tested with scripted models; the `integration` tests load the real models and are skipped where `OCR__MODELS_DIR` has none.

Indra OCR is **parked**: its weights' licence (modified OpenRAIL-M) rules out our use, see `docs/adr/0002-indra-ocr-engine-and-its-licence.md`.
The adapter stays on the `ocrtest` branch. Until a second Hindi engine is chosen, Hindi fields have one reader and come out NEEDS_REVIEW.
The EasyOCR adapter is not ported.

## CLASSIFY, READ, VERIFY, ASSEMBLE (EXT-007)

`ExtractDocumentService` follows IDP-03 ch. 2.6 stages 5 to 7. Engines A and B read page 0 at the same time (in two threads; an engine that fails is retried once, then left out). The page is classified from keyword hits. Each field of the matching template is decided from A and B: accepted when they agree and the rule passes. Engine C then reads only the crop of each unresolved field, and the field is decided again with two of three (`OCR_2_OF_3`). Anything still unresolved is NEEDS_REVIEW, because AI_ASSIST is not built. Then it builds the IDP-00 ch. 7.8 result. The two standard templates (`standard_templates.py`) were checked on the sample cards. Deliberate points to confirm:

- **Templates are code, not JSON yet.** The dataclasses mirror IDP-03 ch. 2.5; loading and validating the stored JSON belongs to the template feature.
- **Anchor shift (interpretation).** An anchor's `region` is where it normally sits; fields marked `relative_to` move by how far the found anchor is from that place. IDP-03 does not define the arithmetic.
- **Aspect ratio lowers confidence, it does not refuse.** The sample Aadhaar crops are about 3:1; their words identify them.
- **A field needs two agreeing engines.** With one engine configured every field is NEEDS_REVIEW. `source` is null when engines disagree (the schema has no value for it).
- **Masked fields are masked everywhere** (`value`, `normalizedValue`, `displayValue` and `readings`), not only for display. Aadhaar keeps the last four digits. IDP-00 ch. 8 should confirm that stored results may not hold the full number.
- **Hindi is separate template fields** (`name_hi`, `address_hi`), not a `value_hi` on one field. Each field has a `language`; only engines asked to read it (PP-OCRv5 with the Devanagari model, and Indra) read it, so two of them must agree and `source` shows `OCR_2_OF_3`. PP-OCRv6 reads English only. The engines and languages per slot are chosen where the engines are built (`scripts/try_extract.py` for now).
- **Unknown document:** no fields, `documentType.code` null, one item for review. The generic key-value path (F-EX-12) and AI_ASSIST are not built; `AI__MAX_FIELDS_PER_DOCUMENT` is added with AI_ASSIST.

## Beyond the design documents

The Hindi fields, the address and PIN fields, the extra template keys and a few other additions are not in IDP-03 or the Python standard. They are kept on the owner's instruction and listed in `docs/adr/0003-additions-beyond-the-design-documents.md` for review.

## Not here yet, and why

The prototype's EasyOCR engine is not ported to `idp_ocr_engines`: it was clearly the weakest in the benchmark, and which engines fill slots A, B
and C is open decision D-1 (IDP-00 ch. 10). Still to come, each with its build step: templates and document types with their API and tokens (E4), ai-gateway (E5), recheck, deep analysis, the template bundle, metrics and integration tests against real PostgreSQL, RabbitMQ and MinIO (E6).

## Operations

No SLOs, dashboards or alerts yet; each is added, with its runbook, together with the feature it watches.
