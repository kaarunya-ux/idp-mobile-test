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
| 2 | Domain | Formats, routes and the route planner as pure Python; a test that all 30 matrix pairs have a route | done, in review (`feat/conversion-service-route-planner`) |
| 3 | First converters (no AI) | Image ↔ image, PDF ↔ images, Office → PDF, ported from `services/conversions/python_scripts/` with tests | |
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
| Database for the outbox | **Open:** IDP-00 §3.1 says conversion-service has no database, but IDP-03 §3.2 gives it an outbox relay, which needs a table (`OBX-01`). Needs an answer before step 4 |
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
| digital PDF → XLSX | `pdf_tables` (model) → `model_to_xlsx` (XLSX) | D |
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
