# conversion-service: build log

A step-by-step record of building `conversion-service` to the Python Engineering Standard, written to learn from. Each entry covers:

- **Goal**: what this step is for
- **What we did**: files and changes
- **Why**: the standard's rule IDs (e.g. `CFG-01`) and IDP contract sections (e.g. IDP-00 §7.6) that require it
- **How it connects**: where this fits with the Java services and the rest of IDP
- **Try it**: commands you can run yourself
- **Lessons**: mistakes, surprises, things to remember

## Roadmap

Each step is one branch and one reviewable pull request (about 400 changed lines; standard ch. 25).

| # | Step | What it produces | Status |
|---|---|---|---|
| 1 | Skeleton | `services/conversion-service/` with `uv`, settings, logging, a health endpoint, `make check` green. No business logic | merged (PR #1) |
| 2 | Domain | Formats, routes and the route planner as pure Python; a test that all 30 matrix pairs have a route | merged (PR #2) |
| 3a | Image converters | PNG ↔ JPEG, images → PDF, PDF → images; the converter pattern (port, adapters, registry, use case) | merged (PR #3) |
| 3b | Office converters | Word, Excel and PowerPoint → PDF (LibreOffice), CSV ↔ XLSX | merged (PR #4) |
| 3c-1 | Document model: Word and sheets | The document model; Word ↔ Excel/CSV, Excel/CSV → Word | done |
| 3c-2 | PowerPoint routes | PowerPoint → Word/Excel/CSV, Excel/CSV → PowerPoint, images and PDF → PowerPoint | done |
| 3c-3 | PDF data routes | PDF tables → Excel/CSV, PDF → Word, rebuilt on the document model (pdfplumber, no PyMuPDF) | done; 3c-1 to 3c-3 are one PR (`feat/conversion-service-document-model`) |
| 4 | Messaging and storage | Receive `ConversionRequested`, run Celery tasks, MinIO in and out, send `JobProgressed` / `ConversionCompleted` / `WorkFailed` | |
| 5 | The rest | PDF edit plans, exports, previews. AI-assisted routes once ai-gateway exists | |

---

## Entry 0: How the pieces connect (2026-10-19)

**Goal:** understand what conversion-service has to do and who it talks to, before writing any code.

### The big idea

The Java services are the front office: they talk to users, keep the records and decide what work is needed. The Python services are the workshop: they do the heavy work on files. They never call each other's code. They communicate through three agreed channels:

| Channel | What travels | Example |
|---|---|---|
| **RabbitMQ** | Small JSON messages: work orders in, status reports out | "Convert file X to DOCX" → "Done, output at Y" |
| **MinIO** | The files themselves. Messages carry only their location | `idp-originals/<accountId>/<documentId>/<fileId>` |
| **HTTP** | Direct calls. conversion-service only calls ai-gateway, and only for AI-assisted routes | "Repair this table" |

conversion-service has **no public API** (IDP-03 §3.1). Its only HTTP endpoints are `/health/live` and `/health/ready` on port 8001, used by the platform to check that it's running.

### One conversion, start to finish

1. The user uploads `report.pdf` → **document-service (Java)** stores it in MinIO and virus-scans it.
2. The user asks for DOCX → Java checks quota with user-service, creates a job, and publishes `ConversionRequested`.
3. **conversion-service** consumes the message, downloads the PDF from MinIO, converts it, and uploads `report.docx` to MinIO.
4. It reports `JobProgressed` (stages `PREPARE` → `CONVERT` → `PACKAGE`), then `ConversionCompleted`, or `WorkFailed` with an error code.
5. Java updates the job and shows the user progress and a download link.

### The contracts we build against

These are what Java expects from us. They come from IDP-00 §7.6–7.7 and IDP-03 §3, and we use them as written (version 1).

**Envelope.** Every message, in both languages (Python standard §16.4):

```json
{ "messageId": "<uuidv7>", "type": "ConversionRequested", "version": 1,
  "occurredAt": "2026-10-19T10:15:30Z", "producer": "document-service",
  "correlationId": "…", "tenantId": "<accountId>", "subjectId": "<job item id>",
  "payload": { } }
```

**Messages for conversion-service:**

| Message | Direction | Exchange · routing key | Payload |
|---|---|---|---|
| `ConversionRequested` | Java → us | `idp.commands` · `conversion.requested.priority` / `.standard` | `jobId, itemId, type (CONVERSION/PDF_EDIT/PDF_MERGE/EXPORT/PREVIEW), inputs[], target, options/editPlan, outputPrefix` |
| `JobProgressed` | us → Java | `idp.events` · `job.progressed` | `jobId, itemId, stage, progress, sequence` |
| `ConversionCompleted` | us → Java | `idp.events` · `conversion.completed` | `jobId, itemId, outputs[{bucket, key, contentType, size, sha256, pageCount}], usage{pages}` |
| `WorkFailed` | us → Java | `idp.events` · `work.failed` | `jobId, itemId, error{code, retryable, detail}` |

We consume from the queues `conversion-service.conversion-requested.priority` and `.standard`. All queues are quorum queues with a dead-letter queue.

**Storage** (IDP-00 §7.7): we read inputs from `idp-originals/<accountId>/<documentId>/<fileId>` and write outputs to `idp-outputs/<accountId>/<jobId>/<itemId>/<fileName>`. Messages never carry file bytes (`MSG-07`).

### What Java expects from us, and how we deliver it

A **contract** is an agreement about messages, the way an API is an agreement about HTTP calls. It covers three things:

- **Shape:** which fields a message has.
- **Meaning:** what each field means.
- **Behaviour:** what each side promises to do, such as "a duplicate message causes no harm".

Java builds its side against the same contract, so neither team has to wait for the other's code.

| # | Java expects | What we do | Where in our service | Rules |
|---|---|---|---|---|
| 1 | We pick up work from our two queues | Subscribe to `conversion-service.conversion-requested.priority` and `.standard`, priority first | `intake` (FastStream consumer) | IDP-00 §7.6 |
| 2 | We understand the message | Validate the envelope and payload with Pydantic at the edge; reject malformed messages to the dead-letter queue, not in a retry loop | `*Message` schemas in `infrastructure/messaging/` | `OBJ-01`, `MSG-03`, `MSG-06` |
| 3 | We fetch inputs from MinIO | Read each file named in `inputs[]` from `idp-originals`, streamed into a temp folder | storage adapter | `FIL-06`, `FIL-07`, `MSG-07` |
| 4 | We do the requested type of work | `CONVERSION`, `PDF_EDIT`, `PDF_MERGE`, `EXPORT`, `PREVIEW`: plan the route, then run converters in the right worker pool | route planner (domain) + Celery tasks | IDP-03 §3.4, `CEL-05` |
| 5 | Outputs land where Java looks | Write to `idp-outputs/<accountId>/<jobId>/<itemId>/<fileName>`, under the `outputPrefix` Java sent | storage adapter | IDP-00 §7.7 |
| 6 | We report progress | `JobProgressed` with stage (`PREPARE` → `CONVERT` → `PACKAGE`), progress % and an increasing `sequence`. Java ignores anything older than what it has seen, and re-dispatches an item that shows no progress for 15 minutes | outbox → `relay` | IDP-00 §7.5, IDP-02 §3.5 |
| 7 | We report success with the details Java stores | `ConversionCompleted` with every output's `bucket, key, contentType, size, sha256, pageCount` and `usage.pages`. Java uses the pages to settle the user's quota | outbox → `relay` | IDP-00 §7.6 |
| 8 | We report failure with a code Java can act on | `WorkFailed` with `error.code` (for example `CNV-422-PDF-001`), `retryable` and `detail`. Java retries up to 3 times if `retryable` is true, then marks the item failed, and shows the user a message based on the code | outbox → `relay` | IDP-03 §3.8, `ERR-05` |
| 9 | A repeated message causes no harm | RabbitMQ can deliver a message twice. The second delivery must not create duplicate outputs or messages | inbox table + idempotent tasks | `MSG-02`, `CEL-03` |
| 10 | No message is lost | Acknowledge a message only after its work is saved; publish replies from an outbox table, not directly | consumer + outbox | `MSG-01`, `OBX-01` |
| 11 | We stay within time limits | 120 s per document and 10 min per job item; LibreOffice gets 60 s and is recycled on timeout | Celery time limits | IDP-03 §3.7, `CEL-06` |
| 12 | The platform can see we're alive | `/health/live` and `/health/ready` on port 8001 | `health.py` | `DPL-03` |

**What Java does *not* expect from us:**
- An HTTP API (only health endpoints).
- Download links: only document-service issues presigned URLs to users.
- Any access to Java's databases (`DB-01`).

**Why the outbox matters (rows 6–8, 10):** if we published a message directly and then crashed before saving our own state, or saved our state and then crashed before publishing, Java would get the wrong picture. With an outbox, the reply is written as a database row in the same transaction as our state change. A separate `relay` process publishes those rows and retries until RabbitMQ confirms receipt. That's also why the database question below matters.

### The processes conversion-service runs (IDP-03 §3.2)

| Process | Tool | Job |
|---|---|---|
| `intake` | FastStream | Reads `ConversionRequested`, validates it, starts Celery work |
| `worker-office` | Celery + LibreOffice | Office ↔ PDF |
| `worker-pdf` | Celery | Page operations, edits, rendering, merging, compression |
| `worker-data` | Celery | Tables and documents: XLSX, DOCX, PPTX, CSV |
| `worker-ocr` | Celery | Searchable PDFs, scanned documents → DOCX/XLSX |
| `relay` | Python | Publishes outgoing messages from the outbox table |
| health HTTP | FastAPI | `/health/live`, `/health/ready` on port 8001 |

**Why so many processes?** Different work needs different resources. LibreOffice is slow and must be isolated, OCR may need a GPU, and data work is CPU-bound. Separate worker pools let each one scale on its own (`CEL-05`).

### Decisions and open questions

| Topic | Status |
|---|---|
| Message contracts | **Decided:** build against IDP-00 §7.6 v1 as written. Changes go through `idp-contracts` (IDP-00 §9) |
| Shared library `idp-py-platform` | **Decided for now:** it doesn't exist yet, so shared plumbing (settings, logging, envelope) starts inside this service's `core/`, and moves into the shared library when a second service needs it |
| Database for the outbox | **Decided 2026-10-22: yes, a small PostgreSQL `conversion_db`** with three tables: inbox (`MSG-02`), outbox (`OBX-01`) and item status (`CEL-03`). IDP-00 §3.1 said "no database", but IDP-03 §3.2 plans an outbox relay, and the Java backend asked for a "this request is already consumed" table. IDP-00's service table needs updating. Java identifies a request by an AMQP header **`eventId`**; the standard's envelope calls it `messageId` in the body. **Open:** whether these are the same value, and whether a deliberate retry gets a new `eventId` (see Entry 8) |
| Where the service lives | **Decided:** `services/conversion-service/` in this repo. The existing scripts in `services/conversions/` are the source of the conversion logic |

---

## Entry 1: Skeleton (2026-10-19)

**Goal:** a service that starts, answers health checks and passes every quality gate, before it contains any business logic. Getting the foundation right first means every later step only adds features; nothing has to be retrofitted.

### What we did

```
services/conversion-service/
  pyproject.toml   dependencies + ruff, mypy, pytest and import-linter settings
  uv.lock          exact versions of every package (generated by uv, committed)
  Makefile         make check / fmt / run / test
  Dockerfile       two-stage image, runs as a non-root user
  README.md        what the service is, how to run it, every environment variable
  .env.example     safe sample values for local runs
  src/conversion/
    health.py      /health/live and /health/ready on port 8001
    core/config.py     Settings: all configuration in one validated class
    core/logging.py    structured JSON logs
  tests/unit/      15 tests, mirroring src/
```

### Why each piece is there

| Piece | Rule | Why it matters |
|---|---|---|
| `uv` + `uv.lock` | §0.2, `DEP-01` | The lock file pins the exact version of every package, so a laptop, CI and the Docker image all install the same thing. "Works on my machine" bugs disappear. Docker installs with `uv sync --frozen`, which fails rather than silently changing versions |
| `src/` layout | §3.2 | Tests import the installed package, not files that happen to sit in the folder, so a packaging mistake shows up in tests, not in production |
| One `Settings` class | `CFG-01`–`CFG-05` | Configuration is read in one place and checked at startup. A typo like `ENVIRONMENT=prodd` stops the process immediately with a clear error, rather than misbehaving hours later. Nested groups use `__` (`LOGGING__LEVEL`). Every variable is in the README table and `.env.example`; `.env` itself is never committed |
| Structured logs | `OBS-01`, `OBS-02` | One JSON object per line, so a log tool (Loki, per IDP-00 §3.2) can search by field: "all lines where `job_id` = X". Events are names with key-values (`log.info("conversion_completed", job_id=...)`), never f-strings. Uvicorn's own logs go through the same renderer so every line has one shape |
| `/health/live` vs `/health/ready` | `DPL-03` | Two different questions. **Live:** is the process alive? If not, the platform restarts it. **Ready:** can it take work right now? If not, the platform stops sending it work but doesn't restart it. Today both just say `UP`; in step 4, ready will check RabbitMQ and MinIO |
| No API docs | IDP-03 §3.1 | The service has no public API, so `/docs` and `/openapi.json` are switched off: less to expose, nothing to document |
| `make check` | §22.2, ch. 27 | Runs exactly what CI will run, in order: format → lint → types → architecture → tests with coverage. If it's green on your laptop, it's green in CI |
| `ruff` | §22.1 | Formatting plus about 20 rule families: bugs (`B`), security (`S`), naming (`N`), complexity (`C90`), timezone-naive dates (`DTZ`), stray `print` (`T20`) and more |
| `mypy --strict` | `PY-01` | Every function has type hints, and mypy proves they're consistent. It catches a whole class of bugs before the code ever runs |
| `import-linter` | §0.6 step 6, ch. 21.6 | Enforces which parts of the code may import which. Today there's one rule: `core` must not import the entry points. Step 2 adds the real layer rules (the domain stays pure, the API never touches the database) |
| Coverage ≥ 80 % | ch. 27.2 | A floor, not a target: `make check` fails below it. Today: 100 % |
| Dockerfile | ch. 23, `DPL-01` | Two stages: build with `uv`, then copy into a slim image that runs as user `10001`, not root. Dependencies are installed before the source is copied, so changing code doesn't reinstall every package |

### How it connects

The platform (Docker Compose today, possibly Kubernetes later, per decision D-6) calls `/health/live` and `/health/ready` on port 8001 to decide whether to restart the container or send it work. conversion-service is **not** registered in Eureka, because it has no HTTP API for anyone to call (IDP-01 §3.2). Java never calls it directly; that starts with messages in step 4.

### Try it

```bash
cd services/conversion-service
uv sync                                   # creates .venv from uv.lock
make check                                # all five gates
make run                                  # then, in another terminal:
curl http://127.0.0.1:8001/health/live    # {"status":"UP","checks":{}}
LOGGING__FORMAT=console make run          # readable logs instead of JSON
ENVIRONMENT=prodd make run                # see the startup validation refuse it
```

### Lessons

- **The linter flags "magic numbers" in tests too.** `PLR2004` complained about `== 200`. In app code an unexplained number should be a named constant; in tests, a literal expected value is the point. We exempted tests in `pyproject.toml` with the reason written next to it (`PY-14`), and used `HTTPStatus.OK` anyway because it reads better.
- **The test client doesn't run startup and shutdown code.** Coverage showed the lifespan hook untested. We added a test that runs `app.router.lifespan_context(app)` directly and checks both log events.
- **Generated files sneak in.** The coverage run leaves a `.coverage` file, so it went into `.gitignore` before the first commit. Always check `git status` before committing.

### Notes and deviations

- `HealthResponse` lives in `health.py`, not in a feature's `api/schemas.py`. Health isn't a feature, and IDP-03 §3.3 puts `health.py` at the package root.
- Base images are pinned by tag. `DPL-01` also asks for monthly rebuilds; pinning by digest comes with CI.
- `pre-commit` (§0.5) isn't set up yet. It arrives with CI, so both run the same checks.

### Setup: installing `uv`

The standard's install command (§0.5) is `curl -LsSf https://astral.sh/uv/install.sh | sh`.

**Lesson:** VS Code on Ubuntu runs as a **snap**, which changes some environment variables inside its terminal. The installer therefore put `uv` in `~/snap/code/256/.local/bin`, a private VS Code folder that can change when VS Code updates, and added a line pointing there to `~/.bashrc`, `~/.profile` and `~/.zshrc`. Fix: move `uv` and `uvx` to `~/.local/bin` (already on the PATH via `~/.profile`) and remove the three added lines. To avoid it, run the installer from a normal terminal, or set `UV_INSTALL_DIR="$HOME/.local/bin"` first.

---

## Entry 2: Domain: formats, routes and the route planner (2026-10-21)

**Goal:** teach the service to *plan* a conversion before it can perform one. Given a source and a target format, the planner returns the ordered steps, or a clear error. It's pure logic: no files, no network, no frameworks.

### What we did

```
src/conversion/
  core/error_codes.py        ErrorCode enum: CNV-422-ROUTE-001, with its HTTP status
  core/errors.py             DomainError: base class of every business error
  conversion/                the first feature (IDP-03 ch. 3.3 names it "conversion")
    domain/formats.py        Format (PDF, DOCX, XLSX, CSV, PPTX, PNG, JPEG) and Representation
    domain/route.py          Converter, Fidelity, Step, Route
    domain/route_planner.py  RouteRequest, RoutePlanner and the route table
    domain/exceptions.py     RouteNotFoundError, InvalidRouteError
tests/unit/                  61 new tests (76 in total), 100 % coverage
```

### How it works

**Two hubs keep the table small** (IDP-00 ch. 5, IDP-03 ch. 3.4). Seven formats give 42 possible pairs, but we don't write 42 converters:

- **PDF is the visual hub:** anything → PDF → images or slides.
- **The document model is the data hub:** pages → text blocks and tables, with positions. Every format is *read into* the model and *written out of* it.

That's why `Representation` has one more member than `Format`: `DOCUMENT_MODEL`, which a step can produce or consume but a user can never ask for.

**A route is a chain of steps:**

| Request | Route | Fidelity |
|---|---|---|
| CSV → PDF | `xlsx_csv` (XLSX) → `office_to_pdf` (PDF) | H |
| digital PDF → XLSX | `pdf_tables` (model) → `model_to_xlsx` (XLSX); renamed `pdf_to_model` in step 3c-3 | D |
| scanned PDF → XLSX | `ocr_to_model` (model) → `model_to_xlsx` (XLSX) | D |
| JPEG → PDF | `images_to_pdf` (PDF) | E |
| JPEG → PDF, `ocr=ON` | `searchable_pdf` (PDF) | E |
| DOCX → PPTX | `office_to_pdf` → `pdf_to_images` → `pptx_from_images` | A |
| DOCX → DOCX | `RouteNotFoundError`: `CNV-422-ROUTE-001` | |

**Variants.** Most pairs have one route. A few depend on facts about the request:
- **`SCANNED`:** a PDF with no text layer has to be read with OCR. The planner learns this from `RouteRequest.pdf_is_scanned`, which the PREPARE stage will set after inspecting the file.
- **`OCR`:** the user set `ocr=ON`, so an image → PDF conversion gets an invisible text layer.

The planner tries the variant first and falls back to the standard route, so a scanned PDF → PNG simply uses the normal rendering route.

### Why it's built this way

| Choice | Rule | Why it matters |
|---|---|---|
| Plain dataclasses and enums, no Pydantic | `OBJ-04`, `PY-03`, `PY-04` | The domain is the part of the code that changes least and must be easiest to test. Frameworks belong at the edges |
| `Route` checks its own chain when it's created | `DOM-02` | A broken route (step 2 reads XLSX but step 1 wrote CSV) **can't exist**; creating one raises `InvalidRouteError`. If someone edits the table wrongly, the service fails at startup and in tests, not halfway through a user's job |
| `RouteNotFoundError` carries `ErrorCode.ROUTE_NOT_SUPPORTED` | `ERR-02`, `ERR-05`, `DOM-03` | Every business error has a stable code. In step 4 the consumer turns it into `WorkFailed { error.code: "CNV-422-ROUTE-001" }`, and Java maps that code to a message for the user |
| `get_route` raises; it never returns `None` | §4.3 verb vocabulary | `get_*` means "must exist, raises if not"; `find_*` would return `None`. The name tells callers what to expect |
| The table is data, built by small functions | `PY-11` | Each function stays under 50 lines and complexity 10, and the table reads like the IDP-03 §3.4 table it comes from |
| "Domain is pure" import-linter contract | `DOM-01`, ch. 21.6 | The domain may not import FastAPI, Pydantic, SQLAlchemy, httpx, Celery, FastStream, Redis, boto3, structlog, or our own config and logging. This is enforced automatically on every `make check`, not left to code review |

### How it connects

- **Java → planner:** document-service validates job options (IDP-02 §3.6): `target`, `ocr`, `pageRange`, and so on. In step 4, `ConversionRequested.target` and `options.ocr` become a `RouteRequest`.
- **Planner → workers:** each `Converter` becomes a Celery task in the right worker pool in steps 3–4: `office_to_pdf` on `worker-office`, OCR steps on `worker-ocr`, and so on.
- **Errors → Java:** `RouteNotFoundError` becomes `WorkFailed` with `retryable: false`, so Java won't retry something that can never work.

### Try it

```bash
cd services/conversion-service
make check
uv run pytest tests/unit/conversion -v        # see every matrix cell tested by name
uv run python -c "
from conversion.conversion.domain.formats import Format
from conversion.conversion.domain.route_planner import RoutePlanner, RouteRequest
print(RoutePlanner().get_route(RouteRequest(source=Format.DOCX, target=Format.PPTX)))"
```

### Lessons

- **Test against the spec, not against the code.** The main test copies the conversion matrix *from IDP-00* into the test file and checks every cell. A test built from the route table itself would happily confirm a wrong table.
- **Prove that a rule can fail.** A rule you've never seen fail proves nothing. We dropped a temporary file importing `structlog` into the domain, watched import-linter report `Domain is pure BROKEN` with the exact file and line, then removed it.
- **Let the formatter format.** `make check` failed on two hand-wrapped lines. `make fmt` fixed them, so nobody argues about formatting in code review.

### Decisions and deviations

| Topic | Decision |
|---|---|
| Planner location | `domain/`, not `application/` as IDP-03 §3.3 suggests: it's a pure business rule (standard ch. 2.4). Agreed before building |
| Reader names | IDP-03 names the writers (`model_to_docx`, ...) but not the readers, so we named them `docx_to_model`, `sheet_to_model`, `pptx_to_model`, `ocr_to_model` |
| 30 cells → 42 pairs | IDP-00 treats PNG and JPEG as one column. In code they're separate formats, so the 30 matrix cells become 42 concrete pairs (including PNG ↔ JPEG) |
| Same-format work | PDF → PDF (optimise or repair, and scanned → searchable, F-CV-03) isn't a route; it's `PDF_EDIT` work in step 5 |
| `ocr=AUTO` for image → PDF | Behaves like `OFF`; IDP-03 only says `ocr=ON` produces a searchable PDF. **Open:** check with product whether AUTO should detect text instead |
| `ErrorCode` and `DomainError` in `core/` | `PKG-02` says core waits until two features need something, but ch. 3.4 explicitly puts the error base classes in core, and every future feature (pdfedit, export, preview) needs them |
| Layers contract | Waits until the feature has an `application/` layer (step 3 or 4); with only `domain/` there's nothing to layer |

---

## Entry 3: Image converters, and the converter pattern (step 3a, 2026-10-21)

**Goal:** the first real conversions: PNG ↔ JPEG, images → PDF and PDF → images. More importantly, the **pattern** every later converter follows, so steps 3b and 3c (and the interns) only add adapters.

### What we did

```
src/conversion/
  bootstrap.py                       composition root: registry + ConvertFileService, built once per process
  core/config.py                     + LimitsSettings (max pages, max pixels, max render size)
  core/error_codes.py                + CNV-422-PDF-001/002, LIMIT-001, FILE-001 (new), OPTIONS-001 (new)
  conversion/domain/options.py       PageRange ("1-3,5"), ConversionOptions (dpi, JPEG quality)
  conversion/domain/ports.py         ConverterPort, ConverterLookupPort, StepRequest, StepResult
  conversion/domain/exceptions.py    + errors for bad options, damaged or locked files, and size limits
  conversion/application/            ConvertFileCommand, ConvertFileResult, services/convert_file.py
  conversion/infrastructure/
    converters/pillow_images.py      safe image opening (damaged files, decompression bombs)
    converters/image_formats.py      PillowImageFormatConverter   (ported from jpg_to_png.py)
    converters/pdf_to_images.py      PdfiumPageRenderer           (ported from pdf_to_png.py / pdf_to_jpg.py)
    converters/images_to_pdf.py      Img2PdfImageWrapper          (lossless, as IDP-03 specifies)
    registry.py                      ConverterRegistry: Converter -> adapter
tests/unit/                          60 new tests (136 in total), 100 % coverage
```

### How the pattern works

```
ConvertFileService (application)                       uses only the PORTS (protocols in the domain)
  1. RoutePlanner.get_route(...)            -> Route: [step 1, step 2, ...]
  2. for each step:
       registry.get(step.converter)          -> the adapter (ConverterPort)
       adapter.convert(StepRequest)          -> StepResult (files written, pages processed)
       the outputs become the next step's inputs; intermediate files stay in a temp folder
  3. only the last step writes into the output folder

bootstrap.py (composition root)                         the ONLY place that knows the concrete adapters
  ConverterRegistry([PillowImageFormatConverter, PdfiumPageRenderer, Img2PdfImageWrapper])
```

To add a converter in 3b or 3c, write one adapter class with a `converter` property and a `convert(request)` method, register it in `bootstrap.py`, and test it. Nothing else changes.

### Why it's built this way

| Choice | Rule | Why it matters |
|---|---|---|
| Ports in the domain, adapters in infrastructure | ch. 2.4, ch. 7 | The application layer talks to "a converter", never to Pillow or PDFium. Swapping a library (as we did with PyMuPDF) touches one adapter. Two import-linter rules enforce this |
| `bootstrap.py` is the only place that wires adapters | `DI-01`, `DI-03` | One wiring for every entry point: tests now, the RabbitMQ consumer and Celery workers in step 4 |
| `ConvertFileService` is synchronous | ch. 15.1 | Conversions are CPU-bound and run in Celery workers, which are sync end to end. (`APP-01`'s "async method" is for API services; noted as a deliberate deviation) |
| Sizes and limits in settings | `ML-07`, `FIL-05`, IDP-03 §3.5/3.7 | Max pages (1,000), max pixels (180 million) and max render size (5,000 px) can change per environment without a code change |
| Images checked before decoding | `FIL-05` | A tiny file can claim billions of pixels and exhaust memory (a decompression bomb). We read the size from the header and refuse it before decoding |
| Temp folders always cleaned up | `FIL-06` | Intermediate files live in `TemporaryDirectory()`; only final outputs reach the output folder |
| PDF output streamed to disk | `FIL-07` | img2pdf writes straight into the file instead of building the whole PDF in memory |
| Library errors become domain errors | `ERR-02`, `ERR-07` | A locked PDF becomes `PasswordRequiredError` (`CNV-422-PDF-001`) and a damaged one `CorruptPdfError` (`CNV-422-PDF-002`), chained with `from exc` so the original cause is kept for debugging |
| Converter libraries in a `workers` extra | `ML-09`, `DPL-05` | The health server's image doesn't install Pillow, PDFium or img2pdf. An import-linter rule stops `health.py` from ever importing them |
| Untyped libraries confined to one adapter each | `PY-01` | pypdfium2 and img2pdf have no type hints. The mypy override names them explicitly, and each adapter returns typed values |
| `PageRange` tested with Hypothesis | `TST-08` | Instead of a few hand-picked cases, Hypothesis generates hundreds of random ranges and checks the rules always hold: inside the document, no repeats |

### How it connects

- **Java → options:** the job's `pageRange`, `dpi` and `imageFormat` options (IDP-02 §3.6) become `ConversionOptions`. Invalid values become `CNV-422-OPTIONS-001`.
- **Errors → Java:** every error here becomes `WorkFailed` in step 4. **Two codes are new**, not yet in IDP-03 §3.8: `CNV-422-FILE-001` (damaged or unsupported image) and `CNV-422-OPTIONS-001` (bad option). Java must add messages for them; this needs telling the Java team.
- **`page_count` → usage:** each result reports the pages it processed. In step 4 this becomes `usage.pages` in `ConversionCompleted`, which Java uses to settle quota. Exactly which count is billed (original pages, or pages output) still needs confirming against the contract.

### Try it

```bash
cd services/conversion-service
uv sync --all-extras       # installs the worker libraries too
make check
uv run pytest tests/unit/conversion/infrastructure -v
```

Then convert a real photo with the snippet at the end of the service README.

### Lessons

- **Check the real library API before writing code.** pypdfium2 5 differs from older examples online. We printed its signatures first, and found that errors carry `err_code` (4 = password, 3 = damaged file), which is how we tell those two apart.
- **A test that won't pass may be telling you the code is wrong.** The test for "image too big for a PDF page" never passed. img2pdf's default (`allow_oversized=True`) keeps such pages valid using the PDF's UserUnit scaling, so our error branch was dead code. We deleted it and tested the real behaviour instead.
- **Coverage gaps point at untested behaviour.** The last uncovered lines were colour-profile preservation and "missing file is our bug, not yours". Both are now tested.
- **Damaged files fail in more than one place.** A JPEG cut short fails while *opening* (header); a PNG with cut pixel data fails while *decoding*. Both must become `CNV-422-FILE-001`, and a missing file must stay `FileNotFoundError`, because that one is our bug.
- **Security-linter false positives deserve a real fix, not a silence.** `S105` flagged `PDF_PASSWORD_REQUIRED` as a "hardcoded password" because of the name. Renaming it to `PDF_ENCRYPTED` (same code) was clearer than a `# noqa`.

### Decisions and deviations

| Topic | Decision |
|---|---|
| PyMuPDF (AGPL) | **Not used.** The interns' PDF scripts were moved to pypdfium2 (approved). AGPL can require publishing the service's source, so pdf2docx (built on PyMuPDF and named in IDP-03 for PDF → DOCX) and the layout code need a company or legal decision before step 3c |
| img2pdf | **Approved by the Python lead on 2026-10-21.** Not on the standard's list, but named in IDP-03 §3.4. LGPL-3.0 is fine for library use. Recorded in `pyproject.toml` |
| New error codes | `CNV-422-FILE-001` and `CNV-422-OPTIONS-001` are added; tell the Java team |
| Registry meaning | IDP-03 has the registry map (source, target) → route. The domain planner already does that, so the registry maps `Converter` → adapter |
| Interns' `image_to_pdf.py` | It's an AI path (the model rebuilds the text), not IDP-03's plain `images_to_pdf`. It belongs with the AI and OCR routes later |
| Pages over 200 inches | Kept valid with img2pdf's UserUnit scaling (its default), not refused |

---

## Entry 4: Office converters: LibreOffice, and CSV ↔ XLSX (step 3b, 2026-10-21)

**Goal:** Word, Excel and PowerPoint → PDF, and CSV ↔ Excel. With step 3a's converters, the two-step routes now work too: CSV → PDF, Office → PNG or JPEG, CSV → images.

**Licensing decision taken before this step:** the Python lead decided on **free, permissively licensed libraries only**: no PyMuPDF, no pdf2docx (MIT itself but built on AGPL PyMuPDF), no Ghostscript, no paid SDKs. LibreOffice (MPL-2.0), openpyxl, python-docx and python-pptx (MIT) all fit.

### What we did

```
core/config.py                       + OfficeSettings: OFFICE__BINARY, OFFICE__TIMEOUT (a timedelta, CFG-02)
core/error_codes.py                  + CNV-504-TIMEOUT-001; CNV-422-FILE-001 now means "damaged or unsupported file"
conversion/domain/cell_values.py     the CSV typing rules, pure: parse_number, plan_columns, number_format, has_header
conversion/domain/options.py         + the sheet option
conversion/domain/exceptions.py      + UnreadableDocumentError, SheetNotFoundError, ConversionTimeoutError
infrastructure/converters/
  office_to_pdf.py                   LibreOfficePdfConverter   (ported from word_to_pdf.py)
  xlsx_csv.py                        OpenpyxlCsvConverter      (ported from csv_convert.py, without the AI step)
bootstrap.py                         + both converters registered
README.md                            + "System dependencies" (LibreOffice, Noto fonts), new settings
tests/                               57 new tests (193 in total), 100 % coverage
```

Measured on this laptop: Word, Excel or PowerPoint → PDF takes about **1 second** (the IDP-00 §8 target is under 10 s for 10 pages). Hindi text renders with **Noto Sans Devanagari** embedded in the PDF.

### How the LibreOffice adapter works

```
LibreOfficePdfConverter.convert(request)
  temp folder ─┬─ profile/  a throwaway LibreOffice profile, unique to this run
               └─ out/      where LibreOffice writes the PDF
  soffice -env:UserInstallation=<profile> --headless --norestore --nolockcheck --convert-to pdf --outdir out <file>
     started in its own process group, with a fixed argument list and no shell
     past OFFICE__TIMEOUT: the whole group is killed  -> ConversionTimeoutError (CNV-504-TIMEOUT-001)
  no PDF produced (LibreOffice exits 0 even when it can't open a file)  -> UnreadableDocumentError (CNV-422-FILE-001)
  PDF moved to the output folder; pages counted with PDFium
```

### How CSV → XLSX decides types (ported rules)

| Cell text | Becomes | Why |
|---|---|---|
| `1,250.50` | the number 1250.5, shown as `#,##0.00` | Numbers keep how they were written |
| `₹950`, `12.5%` | 950 shown as `"₹"0`; 0.125 shown as `0.0%` | Currency and percent survive |
| `0471`, `007` | text | A leading zero means an ID, PIN or phone number |
| `4111111111111111` | text | **More than 15 significant digits:** Excel would round it (see lessons) |
| `1.250,50` in a file using decimal commas | text, right-aligned | A number format can't show a decimal comma, so the value is kept exactly |
| `=HYPERLINK(...)` | text | Never becomes a live formula (see lessons) |

A column becomes numbers only when at least 95 % of its filled cells parse (`NUMERIC_SHARE`). Otherwise everything in it stays text.

### Why it's built this way

| Choice | Rule | Why it matters |
|---|---|---|
| A throwaway profile per LibreOffice run | IDP-03 §3.7 | Runs don't share state, so they can run in parallel, and a crash can't corrupt a profile the next run uses. A fresh profile also keeps LibreOffice's default "high" macro security, so document macros never run |
| Kill the whole process group on timeout | IDP-03 §3.7, `CEL-06` | `soffice` starts a child process (`soffice.bin`). Killing only the parent leaves the child running forever and eating CPU |
| A fixed argument list, no shell | `SEC-08` | A file name can never be interpreted as a command. The `# noqa: S603` says exactly that |
| CSV typing rules in the domain | ch. 2.4, `TST-08` | They're pure business rules ("leading zeros mean an ID"), so they're unit- and property-tested without files |
| `Decimal` in the domain, `float` only when writing XLSX | `PY-06` | Values stay exact while we reason about them. The XLSX format itself stores binary doubles, and we only convert numbers of 15 digits or fewer, which doubles hold exactly |
| Write-only workbook | `FIL-07` | openpyxl streams rows to disk instead of building the whole workbook in memory, so a 1-million-row CSV doesn't exhaust the worker |
| `types-openpyxl` stubs, plus two narrow `# type: ignore` with reasons | `PY-01`, `PY-14` | The stubs are missing two things that exist at runtime. Each ignore names the exact code and says why |
| System packages in the README | §25.3 | LibreOffice and fonts aren't pip packages, so they're documented where people set up a machine, and the worker image installs them in step 4 |

### How it connects

- **Java's `sheet` option** (IDP-02 §3.6) selects the sheet for XLSX → CSV. A sheet that doesn't exist becomes `CNV-422-OPTIONS-001`, so the user learns which option was wrong.
- **`CNV-504-TIMEOUT-001`** will be `retryable: true` in `WorkFailed` (step 4), because a timeout may be temporary load. A damaged file is `retryable: false`.
- **The `office` worker pool** (IDP-03 §3.2) is where `office_to_pdf` will run in step 4, with its own concurrency.

### Try it

```bash
cd services/conversion-service
uv sync --all-extras
make check                                                   # includes 3 real LibreOffice runs, about 6 s total
uv run pytest tests/unit/conversion/infrastructure/converters/test_office_to_pdf.py -v
```

### Lessons

- **Excel silently corrupts long numbers.** Excel keeps 15 significant digits, so a 16-digit card or account number without a leading zero (`4111111111111111`) becomes `4.11111E+15` and the last digits are lost. The interns' rules (and many converters) have this bug. We found it because mypy refused `Decimal` values; thinking about the `float` conversion led straight to it. Numbers longer than 15 digits now stay text.
- **openpyxl turns text starting with `=` into a formula.** A CSV cell `=HYPERLINK("http://evil")` would become a live, clickable formula ("formula injection"). Text cells are forced to type `s` (string), and a test proves it.
- **LibreOffice says "success" even when it fails.** It exits with code 0 when it can't open a file, so we check that the PDF actually exists.
- **Stub gaps aren't code bugs.** The openpyxl stubs lack `column_dimensions` on write-only sheets and type `iter_rows` oddly. We checked at runtime that both work, then used narrow, explained ignores rather than silencing mypy for the whole file.
- **A safety check in your own script can save you.** While escaping the Unicode minus sign, an `assert` in the editing script refused to write because the new comment still contained the character. Nothing half-done was written.
- **An unreachable line is a design smell.** `_decode` ended with a `raise` that could never run, because Latin-1 decodes anything. We restructured so Latin-1 is the explicit last resort, and tested it with a byte that is invalid in both UTF-8 and Windows-1252.

### Decisions and open questions

| Topic | Decision |
|---|---|
| One LibreOffice process per document | IDP-03 plans a pool of long-running `unoserver` instances, recycled every 200 conversions. One process per document (about 1 s) is simpler and meets the target; `unoserver` is an optimisation for production hardening |
| Excel "fit to page" (IDP-00 §5) | **Deferred.** It needs editing the sheet's page setup. Doing that with openpyxl would drop charts and images from the workbook, so it waits for LibreOffice's own API (UNO, with `unoserver`) |
| XLSX → CSV writes **stored** values | `1250.5`, not `1,250.50` as displayed. Excel's "Save as CSV" writes displayed values. Stored values are the exact data, matching IDP's "E" rating. **Open:** confirm with product |
| CSV encoding and delimiter on output | UTF-8 without BOM, comma-separated. IDP-03 mentions BOM and delimiter options, but IDP-02's job options don't carry them yet, so this waits for the contract |
| No title row in CSV → XLSX | The interns' script adds a title row from the file name; that breaks the exact CSV ↔ XLSX round trip, so it's left out |
| Header detection | The ported rule takes a first row with words as a header even when it's data. The data can't tell them apart; the cost is header styling on one row, never lost data |
| Usage for tables | A CSV or XLSX conversion counts as 1 page in `page_count`, pending the usage contract (step 4) |
| python-docx and python-pptx | MIT, approved under the permissive-only decision. Dev-only for now (to build test files); they become worker libraries in 3c |

---

## Entry 5: The document model, and Word ↔ Excel/CSV (step 3c-1, 2026-10-22)

**Goal:** the first *data* conversions, where the content stays editable instead of becoming a picture or a PDF. Step 3c-1 adds the shared middle format (the **document model**), readers for Word, Excel and CSV, and writers for Word, Excel and CSV. Four matrix pairs now work: **DOCX → XLSX, DOCX → CSV, XLSX → DOCX, CSV → DOCX**.

### What we did

```
conversion/domain/document_model.py     DocumentModel -> Page -> Block (TextBlock | Table); validated on creation
conversion/domain/exceptions.py         + NoTableFoundError
core/error_codes.py                     + CNV-422-TABLE-001 (a table export from a document without tables)
core/config.py                          + LIMITS__MAX_DOCUMENT_TABLE_ROWS (20,000)
infrastructure/model_file.py            the model as versioned JSON (<stem>.model.json), read back through Pydantic
infrastructure/converters/
  spreadsheets.py                       shared CSV/XLSX helpers, moved out of xlsx_csv.py so the new converters reuse them
  xlsx_csv.py                           now a thin adapter over spreadsheets.py (same behaviour, same tests)
  docx_to_model.py                      PythonDocxReader     Word -> model
  sheet_to_model.py                     SheetReader          CSV/XLSX -> model
  model_to_docx.py                      PythonDocxWriter     model -> Word
  model_to_sheets.py                    OpenpyxlModelWriter, CsvModelWriter    model -> XLSX / CSV
bootstrap.py                            + the five new converters registered
pyproject.toml                          python-docx moved from dev to the workers extra; import rules cover `docx`
tests/                                  49 new tests (242 in total), 100 % coverage
```

### How the document model works

```
DocumentModel
  └─ Page (name: "Sales" for a sheet, None for a Word document)
       └─ blocks, in reading order:
            TextBlock(text, kind = HEADING | PARAGRAPH | LIST_ITEM | CAPTION, level 1–6, alignment, bold)
            Table(rows of text, header_rows)
```

Every data route is **reader → model → writer**. DOCX → XLSX is `DOCX_TO_MODEL` then `MODEL_TO_XLSX`, and the route planner from step 2 already chains them. Because readers and writers only meet at the model, a new format needs one reader and one writer, not one converter per pair. 3c-2 (PowerPoint) and 3c-3 (PDF) plug into the same model.

What each reader keeps:

| Source | Becomes |
|---|---|
| Word "Title", "Heading 1–9" | `HEADING`, level 1–6 (Word goes to 9; deeper levels are capped at 6) |
| Word "List Bullet 2", or numbering depth (`w:ilvl`) | `LIST_ITEM` with its depth |
| Word "Caption" | `CAPTION` |
| Centred / right alignment, bold paragraphs | kept; justified reads as left |
| Merged table cells | the text once, in the top-left cell; the other cells are empty |
| Rows Word repeats on each page (`w:tblHeader`), or else a bold first row | `header_rows` |
| A CSV file | one unnamed page with one table |
| An XLSX workbook | one page per sheet with data, named after the sheet; empty edges trimmed |

What each writer produces:

| Target | Output |
|---|---|
| Word | Page name as Heading 1, Word's own styles for each role (headings in black), tables in "Table Grid" with bold header rows that repeat on each printed page |
| XLSX | One sheet per table, typed with step 3b's CSV rules (numbers, currency, IDs stay text). Named after its page when the page has one table, else "Table N" |
| CSV | One table: the first one, or the one on the page named by the `sheet` option |

### Why it's built this way

| Choice | Rule | Why it matters |
|---|---|---|
| The model is plain frozen dataclasses in the domain, checked when created | ch. 2.4, `DOM-01`, `DOM-02`, `PY-03` | An empty heading or a ragged table can't exist, so every writer can trust its input. No library types leak into it |
| Steps hand over a JSON file, not a Python object | IDP-03 ch. 3.4 | In step 4 a reader and a writer may run in different worker processes. A file is what crosses that gap |
| Pydantic DTOs only in `model_file.py`, `extra="forbid"`, `schema_version: 1` | `OBJ-01` | A model file from a different version, or a typo in a field, fails loudly at the boundary instead of producing a quietly wrong document |
| Shared helpers moved to `spreadsheets.py` | one rule, one place | XLSX → CSV, CSV → XLSX and model → XLSX all type cells the same way, so a fix lands everywhere at once |
| A row limit for tables written into Word | IDP-00 §8 (limits) | A 1-million-row sheet would make an unusable, gigabyte-sized Word file and exhaust the worker. Past the limit the error says to use XLSX or CSV (`CNV-422-LIMIT-001`) |
| `CNV-422-TABLE-001` for "no tables" | `ERR-05` | "Word → Excel" on a letter without tables is a user error with a clear fix, not a crash. Java can show a precise message |
| `docx` added to the import rules | ch. 3.3 | The domain, the application layer and the slim health server still can't import a converter library |

### How it connects

- **Java's `sheet` option** now also picks which table goes into a CSV when a document has several. A name that doesn't exist is `CNV-422-OPTIONS-001`; a page with no table is `CNV-422-TABLE-001`.
- **New error code for Java:** `CNV-422-TABLE-001`. Add it to the list for the Java team with `CNV-422-FILE-001` and `CNV-422-OPTIONS-001`.
- **The model file is internal.** It lives in the conversion's temp folder and never reaches MinIO or Java.

### Try it

```bash
cd services/conversion-service
make check
uv run pytest tests/unit/conversion/infrastructure/converters/test_docx_to_model.py -v
uv run pytest tests/unit/conversion/infrastructure/converters/test_model_writers.py -v
```

### Lessons

- **A middle format pays off from the second format onward.** Four routes needed only three readers and three writers, and PowerPoint and PDF will reuse all three writers.
- **Refactor before you copy.** The XLSX writer needed the cell typing from `xlsx_csv.py`. Moving it into `spreadsheets.py` first, with the old tests still passing, proved the move changed nothing before anything new used it.
- **Word's merged cells repeat.** python-docx returns the same cell object for every grid position a merged cell covers, so a naive read writes "Wide Wide". The reader checks the underlying XML element (`_tc`) and keeps the text once.
- **python-docx has no API for some things.** List depth from numbering and repeating header rows needed small, tested XML reads and writes (`w:ilvl`, `w:tblHeader`).
- **Word's template has three list levels.** "List Bullet 4" doesn't exist in the default template, so deeper items use "List Bullet 3" rather than crashing.

### Decisions and open questions

| Topic | Decision |
|---|---|
| What the model keeps | Text roles, levels, alignment, bold and tables. **Not** fonts, colours, images or italics yet: they don't survive a sheet anyway, and images arrive with the PDF and PowerPoint routes if product needs them |
| Header detection in Word tables | Marked repeating rows win. Otherwise a fully bold first row counts, but only in tables with at least two rows |
| Which table goes into a CSV | The first one, unless the `sheet` option names a page. One CSV file can hold only one table; a ZIP of several CSVs waits for the outputs contract |
| PR size | About 770 lines of source and 530 of tests, above the ~400-line guide (ch. 25). The model, its file format and the first readers and writers only make sense together. The Python lead then chose to ship 3c-1 to 3c-3 as **one PR** (see Entry 7) |

---

## Entry 6: PowerPoint routes (step 3c-2, 2026-10-22)

**Goal:** PowerPoint in and out. Nine more matrix routes now run: **PPTX → DOCX, XLSX, CSV**; **XLSX and CSV → PPTX**; **PNG, JPEG, PDF and DOCX → PPTX** (the last two through `pdf_to_images`, as IDP-03 plans).

### What we did

```
infrastructure/converters/
  pptx_to_model.py       PythonPptxReader   PowerPoint -> model   (ported from ppt_to_office.py, without the AI step)
  model_to_pptx.py       PythonPptxWriter   model -> PowerPoint   (rules from to_pptx.py)
  pptx_from_images.py    PptxImageSlides    images -> PowerPoint, one slide per image
domain/document_model.py + check_table_sizes, moved here from model_to_docx.py: Word and PowerPoint share the rule
bootstrap.py             + the three converters
pyproject.toml           python-pptx moved from dev to the workers extra; import rules cover `pptx`
tests/                   31 new tests
```

### How the reader works

| In the deck | In the model |
|---|---|
| A slide | A page named after the slide title, or "Slide 3" when it has none. The title isn't repeated as text |
| Shapes | Read top to bottom in bands of a quarter inch, then left to right. A group's shapes are read in place of the group |
| A paragraph with a bullet (its own `a:buChar` / `a:buAutoNum`, or from a body placeholder's layout) | `LIST_ITEM` at the paragraph's level |
| Other text | `PARAGRAPH`, with alignment and bold |
| A table | A table; merged cells read once; one header row when PowerPoint's "Header Row" option is on |
| A chart | A caption ("Chart: Sales") and the chart's own data as a table: categories down, one column per series |
| Pictures, SmartArt, speaker notes | Not read yet (see decisions) |

### How the writer lays out slides

A slide doesn't flow like a Word page, so nothing is squeezed onto it. Content that doesn't fit continues on a new slide:

```
page "Sales" (heading, 3 paragraphs, a 31-row table)
  slide 1  "Sales"           the text (up to 10 blocks per slide)
  slide 2  "Sales (cont.)"   table rows 1-14, under the header row
  slide 3  "Sales (cont.)"   table rows 15-28, header repeated
  slide 4  "Sales (cont.)"   table rows 29-30, header repeated
```

- **15 rows per slide**, header rows included (IDP-03 ch. 3.4). Cell text shrinks from 14 pt to 8 pt as tables get wider.
- **16:9 slides.** python-pptx's template is 4:3, so the title box is moved to fit.
- **Real bullets.** python-pptx has no bullet API for text boxes, so the writer adds `a:buChar` to the XML. LibreOffice renders it, which we checked by converting a generated deck to PDF.
- The slide title is the page name, else a leading heading, else the file name (a CSV has no sheet name).

**Images → slides:** each image is embedded byte for byte and fitted without cropping. The deck takes the first image's shape, so a portrait PDF gives portrait slides, within PowerPoint's 1–56 inch limits. A phone photo with an EXIF "rotate" tag is turned upright first, because PowerPoint ignores the tag.

### Why it's built this way

| Choice | Rule | Why it matters |
|---|---|---|
| Continuation slides instead of shrinking | IDP-03 ch. 3.4 | Shrunk text becomes unreadable and runs off the slide. The interns reached the same rule in `to_pptx.py` |
| `check_table_sizes` moved to the domain | `DOM-01`, one rule in one place | It's a business rule ("too long for a document or a deck"), and two adapters need it. Importing one adapter from another would couple them |
| Chart data as a table | IDP-00 ch. 5 ("slide tables") | The numbers behind a chart are data the user owns; a picture of the chart can't go into a sheet |
| Pictures, SmartArt and notes left out | ch. 25 (small steps) | Images need a change to the model and its file (an image block plus the image files). That is its own step, not a side effect of this one |

### Try it

```bash
cd services/conversion-service
uv run pytest tests/unit/conversion/infrastructure/converters/test_pptx_to_model.py \
              tests/unit/conversion/infrastructure/converters/test_model_to_pptx.py -v
```

### Lessons

- **A round-trip test catches design surprises.** Writing a model to PowerPoint and reading it back gave two pages, not one, because text and a table go on separate slides. The behaviour was right; the test's expectation was wrong. Writing it down made the rule visible.
- **python-pptx creates new objects on every access.** `slide.shapes.title is shape` is always false, so the reader compares `shape_id` instead.
- **Placeholders hide their bullets in the layout.** A paragraph in a body placeholder has no bullet in its own XML but shows one on screen. The reader checks the paragraph first, then the placeholder type.

### Decisions and open questions

| Topic | Decision |
|---|---|
| PPTX → DOCX "text + images" (IDP-03) | **Text and tables only for now.** Images need an image block in the model. **Open:** confirm with product whether images are needed for the MVP |
| Hidden slides | Included. Backup slides often hold the data people want in a sheet |
| Speaker notes | Not read. They aren't slide content; easy to add as captions if product wants them |
| Wide tables on slides | Never refused; text shrinks to 8 pt. Splitting columns across slides is possible later |
| PDF → PPTX "+ text boxes from the model" (IDP-03) | Images only for now. Text boxes over the picture come once the PDF reader keeps positions |

---

## Entry 7: PDF data routes (step 3c-3, 2026-10-22)

**Goal:** digital PDF → Word, Excel and CSV without PyMuPDF or pdf2docx. This finishes step 3c: **every route in the matrix that needs no OCR now runs (36 of 47)**. The other 11 (scanned PDF or image → Word/Excel/CSV, and searchable PDF) wait for the OCR decision D-1.

**One PR for 3c:** the Python lead chose to review 3c-1, 3c-2 and 3c-3 together, as one PR from `feat/conversion-service-document-model`. That's about 1,450 lines of source and 1,300 of tests, well above the ~400-line guide (ch. 25). The entries 5–7 split it into reviewable parts.

### What we did

```
domain/route.py                       PDF_TABLES and PDF_TO_DOCX replaced by one step: PDF_TO_MODEL
domain/route_planner.py               digital PDF -> DOCX now goes PDF_TO_MODEL -> MODEL_TO_DOCX
infrastructure/converters/
  pdf_to_model.py                     PdfplumberReader   digital PDF -> model (pdfplumber, MIT)
bootstrap.py                          + the reader
pyproject.toml                        + pdfplumber in the workers extra; import rules cover `pdfplumber`
tests/                                12 new tests; "every non-OCR route can run" replaces a hand-picked list
```

**New library: pdfplumber 0.11** (MIT), with pdfminer.six (MIT), cryptography (Apache-2.0/BSD), charset-normalizer (MIT) and cffi (MIT). All permissive, so it fits the licensing decision.

### Why the route changed

IDP-03 plans `pdf_to_docx` with **pdf2docx**, which requires PyMuPDF (AGPL), so it's out. IDP-03 already sends **scanned** PDFs to Word through the document model (`ocr_to_model` → `model_to_docx`). Digital PDFs now take the same path with a different reader:

```
digital PDF ─ pdf_to_model (pdfplumber) ─┐
scanned PDF ─ ocr_to_model (OCR, later) ─┼─ document model ─┬─ model_to_docx  (A)
                                         │                  ├─ model_to_xlsx  (D)
                                         │                  └─ model_to_csv   (D)
```

One reader serves Word, Excel and CSV. The fidelity ratings don't change: Word is A (layout rebuilt), sheets are D.

### How the reader works

| In the PDF | In the model |
|---|---|
| A page | An unnamed page (Word doesn't print "Page 3" headings; sheets are named "Table N") |
| A ruled table (pdfplumber's "lines" strategy) | A table. The header row uses the same rule as CSV: words above numbers |
| A box of one row or one column | Text, not a table: it's a frame around a notice |
| Lines close together in the same size and weight | One paragraph |
| A line clearly bigger than the body text (×1.15 or more) | A heading. The largest size in the document is level 1, the next is 2, and so on |
| A line starting with a bullet or "2." / "b)" | A list item. Depth counts from the page's least-indented bullet, one level per quarter inch |
| Lines in the middle of the page / ending at the right margin | Centred / right-aligned |

The body size is the size most characters have, measured across the whole document, so heading levels stay the same from page to page. The PDF is opened with PDFium first, so a locked or damaged file gets the same error as in `pdf_to_images` (`CNV-422-PDF-001` / `-002`).

### Why it's built this way

| Choice | Rule | Why it matters |
|---|---|---|
| No pdf2docx, no PyMuPDF | licensing decision (Entry 4) | AGPL would oblige us to publish the service's source to its users |
| Ruled tables only | IDP-03 ch. 3.4 ("camelot lattice for ruled tables") | pdfplumber's "text" strategy finds tables in ordinary paragraphs. A false table in a sheet is worse than a missed one |
| One page at a time, `page.close()` after each | `FIL-07` | A 1,000-page PDF never holds every page's parsed characters in memory at once |
| PDFium checks the file before pdfplumber | `ERR-05` | One error code for one problem, whichever converter meets it |
| Real file tested through LibreOffice | `TST-09` (a bug fix starts with a failing test) | Synthetic PDFs use Helvetica. Real Word PDFs use Symbol-font bullets, which the synthetic test could never show. The test fails without the fix |

### How it connects

- **Scanned or not** is decided in step 4's PREPARE stage (`RouteRequest.pdf_is_scanned`). This reader assumes a text layer; a scanned PDF must never reach it. Step 4 needs a "has a text layer" check (pypdfium2 can count characters per page).
- **`ConverterRegistry` coverage:** the test `test_this_build_can_run_every_route_that_needs_no_ocr` now proves the whole non-OCR matrix is wired, so a converter can't be forgotten in `bootstrap.py`.

### Try it

```bash
cd services/conversion-service
make check                                                 # 280 tests, 100 % coverage
uv run pytest tests/unit/conversion/infrastructure/converters/test_pdf_to_model.py -v
```

### Lessons

- **Test with a real file before calling it done.** All synthetic tests passed, then a Word file → LibreOffice → PDF → our reader lost every bullet. Word's bullets are drawn with the Symbol font, whose bullet is a private-area character (U+F0B7), not "•". The same file showed that Word indents first-level bullets, so measuring depth from the page margin made every list one level too deep. Both are fixed, and the real round trip is now a test.
- **A coverage gap can mean the test doesn't test what you think.** The "frame, not a table" branch was never reached: pdfplumber doesn't report a single box as a table at all. The test passed for a different reason than intended. A one-column box does reach the branch.
- **Tools can change what you write.** The editor turned `•` escapes into literal characters, and ruff flagged the literal en dash as ambiguous. Escapes are now written by a script, and the comment says which characters they are.

### Decisions and open questions

| Topic | Decision |
|---|---|
| IDP-03's `pdf_tables` and `pdf_to_docx` | **Deviation:** replaced by one step, `pdf_to_model`, because pdf2docx is AGPL-bound. Tell whoever owns IDP-03 |
| Unruled tables | Not detected (they come out as paragraphs). **Open:** if real customer PDFs need them, try pdfplumber's text strategy behind a stricter check, or the AI `table-repair` route |
| Multi-column pages | Read top to bottom across columns, so two columns interleave. Acceptable for fidelity A; column detection is a later improvement |
| Images in PDF → DOCX | Not carried over (same image-block question as PowerPoint) |
| Hyphenated line ends | Kept as written ("inter- national"). Joining them risks breaking real hyphenated words |

## Entry 8: RabbitMQ, MinIO, Celery and the outbox (step 4a–4d, 2026-10-26)

**Goal:** the service runs for real. Java's `ConversionRequested` arrives over RabbitMQ, a Celery worker converts files from MinIO, and `JobProgressed` / `ConversionCompleted` / `WorkFailed` go back on `idp.events`. Until now the converters only worked on local files.

**One PR for step 4:** the Python lead chose to review 4a–4d together, from `feat/conversion-service-storage`. That's about 2,350 lines of source and 1,600 of tests, far above the ~400-line guide (ch. 25). The parts below split it for review.

### What we did

```
core/            db.py (engine, DB-02 timeouts), inbox.py, outbox.py (table + relay), clock.py, ids.py (UUIDv7)
                 config.py: DB__, RABBIT__, CELERY__, STORAGE__, OUTBOX__ groups; error_codes.py: 2 new codes
domain/          models.py (ConversionItem and its rules), events.py, values.py, policies.py (which worker pool)
application/     accept_conversion.py (intake), process_conversion.py (worker: download, convert, package, upload)
infrastructure/  persistence/ (SQLAlchemy), messaging/ (FastStream consumer, publisher, wire models),
                 storage.py (MinIO via boto3), inspection.py (scanned-PDF check), tasks.py (Celery)
consumers.py, worker.py, outbox_relay.py      the three processes
migrations/      Alembic; 0001_baseline: inbox_message, outbox_event, conversion_item
Dockerfile       three targets: health, service (intake, relay, migrations), worker (LibreOffice + Noto fonts)
compose.yaml     the whole stack locally (`make up`)
sonar-project.properties, Makefile (lint, check-unit, migrate, up, down; coverage.xml for SonarQube)
```

**New libraries** (all permissive unless noted): SQLAlchemy (MIT), **psycopg 3 (LGPL-3.0)**, Alembic (MIT), uuid-utils (BSD), Celery (BSD), FastStream (Apache-2.0) with aio-pika (Apache-2.0), typer and watchfiles (MIT), boto3 (Apache-2.0). Dev only: testcontainers (Apache-2.0), boto3-stubs and celery-types (MIT).

psycopg is LGPL: we use it unmodified as a library, which LGPL allows without opening our code, and the standard names it as the PostgreSQL driver. If the project wants permissive-only with no exceptions, `pg8000` (BSD) is a drop-in swap. **That's the lead's call.**

### How a request flows

```
Java ─ConversionRequested─▶ idp.commands ─▶ intake ─▶ conversion_db: inbox row + item (one transaction)
                                              └─▶ Celery queue: office | pdf | data | ocr
worker ─▶ MinIO download ─▶ convert ─▶ MinIO upload ─▶ conversion_db: item + outbox rows (one transaction)
relay  ─▶ idp.events (publisher confirm, eventId header) ─▶ Java
```

| A request arrives and the item is… | We… |
|---|---|
| new | record it and queue it |
| already QUEUED | queue it again (repairs a task lost on the way) |
| RUNNING, recently touched | do nothing: a worker has it |
| RUNNING, untouched past the time limit + 2 min | take it over (its worker died) |
| COMPLETED | send the stored `ConversionCompleted` again; no reconversion |
| FAILED | queue it again (Java decided to retry) |

The same `eventId` twice is recorded once in `inbox_message`, so a duplicate never creates a second item.

| Message | Outcome |
|---|---|
| not JSON, no envelope | dead-letter queue (`<queue>.dlq`), for a person to look at |
| readable but unusable (job type, inputs, prefix) | `WorkFailed` `CNV-422-REQUEST-001`, so Java's item doesn't hang |
| the database or the task queue is down | waits in `.retry.10s`, then `.1m`, then `.10m`; after that, dead-letter queue |

### Why it's built this way

| Choice | Rule | Why it matters |
|---|---|---|
| Outbox, not "publish after commit" | `OBX-01` | "Item COMPLETED" and "tell Java" can't come apart: a crash between them would leave Java waiting forever |
| Inbox keyed on `eventId` | `MSG-02` | RabbitMQ delivers at least once; the Java dev asked for exactly this table |
| Item state checked before any work | `CEL-03` | A task delivered twice converts once |
| Publisher confirms + `mandatory` | Java standard ch. 27.4 | A message RabbitMQ didn't accept, or had nowhere to route, is retried, never marked sent |
| One message in flight per item | `OBX-01`, ordering | Java never sees "completed" before "progress 80" |
| Retry queues with TTL, no requeue | `MSG-03` | A broken message can't spin in a hot loop |
| Quorum queues, delivery limit, DLQ | `MSG-04` | Survives a broker node loss; poison messages stop after 5 tries |
| Soft time limit → `CNV-504-TIMEOUT-001` | `CEL-06` | The item fails cleanly before the hard kill |
| Image only for each process | `DPL-05` | The intake image has no LibreOffice (548 MB vs 1.77 GB) |

### Fixes to the Java standard's outbox claim query

Both are in our query; **tell the Java team**, since their relay uses the same one:

1. A `RETRYING` row blocks itself (`NOT EXISTS … status IN ('CLAIMED','RETRYING')` matches the row itself), so once a publish fails, that item's messages are stuck forever. Fix: `AND b.id <> o.id`.
2. `UPDATE … RETURNING` returns rows in **no particular order**, even with `ORDER BY` in the subquery. Two messages of one item can go out reversed. Fix: wrap it in a CTE and `ORDER BY created_at` outside.

### New error codes for Java

| Code | Retryable | Meaning |
|---|---|---|
| `CNV-422-REQUEST-001` | no | The request can't be used: unknown job type, missing input file, unsafe output prefix |
| `CNV-500-INTERNAL-001` | yes | Something failed inside the service after its retries; Java may dispatch again |

### Try it

```bash
cd services/conversion-service
make check        # 376 tests (23 against real PostgreSQL, RabbitMQ and MinIO), 97 % coverage. Needs Docker
make check-unit   # the same without Docker
make up           # the whole stack; RabbitMQ UI on :15672 (guest/guest), MinIO console on :9001
make down
```

### Lessons

- **Integration tests find what unit tests can't.** The real-Postgres tests showed the `RETURNING` order bug, and the end-to-end test showed that an unrouted `JobProgressed` correctly holds back `ConversionCompleted`. That's the right behaviour, but it means **Java must bind queues for every event we send**, or our messages wait and then go `DEAD`.
- **Run the images, not just the tests.** `make check` passed, but the containers failed three ways: a type-stub package (`mypy_boto3_s3`) imported at runtime, which isn't in the image; the missing `faststream[cli]` extra; and Celery's delayed retries, which need topic exchanges and every pool queue declared, or they block the worker. All three are fixed. A new import rule keeps dev-only packages out of runtime code.
- **The image MinIO no longer publishes.** Its official images are gone; tests and `compose.yaml` use the frozen `bitnamilegacy/minio`. That's fine for tests. Java's team runs the real server.
- **A slip:** during this step a stray `git stash` moved all uncommitted edits aside for a moment; `git stash pop` restored them. Nothing was lost. Commit in small steps, so there is less to lose.

### Decisions and open questions

| Topic | Decision |
|---|---|
| Retries from Java | **Assumed:** a deliberate retry (after a retryable `WorkFailed`, or Java's 15-minute reaper) gets a **new** `eventId`. A redelivery keeps the same one. **Confirm with the Java dev** |
| `eventId` vs `messageId` | We use the `eventId` header, falling back to the envelope's `messageId`. We send both, with the same value |
| Event queues on Java's side | **Java must bind** `job.progressed`, `conversion.completed` and `work.failed` on `idp.events`, or our relay retries and then marks rows `DEAD` |
| `mode` job option | Accepted and ignored (IDP-02 §3.6 doesn't define its effect yet) |
| psycopg (LGPL) | Kept, as the standard names it. **Open:** swap to pg8000 if strictly permissive-only |
| OCR pool | Queue exists; requests needing OCR fail with `CNV-422-ROUTE-001` until the OCR decision D-1 |
| Stuck `DEAD` outbox rows | Logged as errors. **Open:** an alert on `outbox_event_dead`, and a runbook for replaying them |
