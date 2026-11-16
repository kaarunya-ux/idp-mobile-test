# IDP Python Services: Integration Readiness

**Date:** 29 October 2026 · **From:** Python team (Prakyath) · **For:** Tech lead and backend (Java) engineers

All three Python services (conversion-service, extraction-service and ai-gateway) are built, tested and merged on
`feat/idp-consolidation`. We are ready to start integration testing with the Java backend. We will provide the
Python stack as Docker containers for local testing first. Before the first session, we need a few things set up on
the backend side and agreement on ten contract points, listed in sections 4 and 5.

---

## 1. The three services at a glance

| Service | What it does | Port | Talks to Java through | State |
|---|---|---|---|---|
| **conversion-service** | Converts files (Office ↔ PDF ↔ images ↔ data), merges PDFs, page previews, PDF editing, exports of results | 8001 (health) | RabbitMQ commands and events, MinIO | 36 of 42 conversion routes built; the 6 routes for scanned documents wait for OCR/AI |
| **extraction-service** | Reads documents into structured fields with templates. Also handles recheck of on-device results, deep analysis, the template designer, the mobile template bundle | 8000 (API) | RabbitMQ, MinIO, and an HTTP API through the Java gateway | Complete against IDP-03 ch. 2 |
| **ai-gateway** | The only service that calls the AI model server: prompts, fair queuing, daily limits per account, circuit breaker | 8002 (internal) | Called by extraction-service only, with a service token. No direct contact with Java | Complete for what extraction needs. A mock model is included for testing |

**Test status** (`make check`, with real PostgreSQL, RabbitMQ, MinIO and Redis in Docker):

| Service | Tests | Coverage |
|---|---|---|
| conversion-service | 479 | 96% |
| extraction-service | 598 | 93% |
| ai-gateway | 92 | 97% |

Each service has one end-to-end test: a message on RabbitMQ goes through the worker, a result is written to MinIO,
and the events come back on `idp.events`.

---

## 2. Features

### conversion-service
- **CONVERSION:** Word, Excel, PowerPoint ↔ PDF; PDF → images; images → PDF; data formats (CSV, XLSX, JSON). The
  route matrix is IDP-00 ch. 5.
- **PDF_MERGE:** several PDFs or images into one PDF, in a given order, with page ranges.
- **PREVIEW:** page thumbnails, created when a file is uploaded.
- **PDF_EDIT:** an edit plan with redaction, text, shapes, form filling, rotate and delete pages, and passwords
  (the passwords arrive encrypted).
- **EXPORT:** extraction results to XLSX or CSV.
- **Not yet:** the 6 routes whose input is a scan or a photo (scanned PDF → Word or Excel). They need the OCR
  engines and the AI model, which we will connect later.

### extraction-service
- **EXTRACTION:**
  - stages: quality gate → preparation → classify → two OCR engines → verification → AI fallback → result;
  - standard templates: PAN, Aadhaar, Indian driving licence;
  - a card's two sides are read together;
  - digital PDFs and Word/Excel/PowerPoint files are read from their text, with no OCR.
- **RECHECK:** an on-device result from the mobile app. The server reads the weak fields again and keeps the rest.
- **DEEP_ANALYSIS:** a stored result and its pages go to the AI. Its findings are added under `analysis`; accepted
  fields are never overwritten.
- **Templates and document types:** a full HTTP API with versions, a publish step, clone, import and export.
- **Template designer:**
  - sample documents;
  - AI field suggestions;
  - test runs with accuracy per field;
  - a publish gate: publishing needs a passing test run, or an override reason.
- **Mobile template bundle:** published templates, rules and thresholds for offline use, with `ETag` / `304`.
- **Account deletion:** on `AccountDeleted`, the account's templates are archived and its sample files deleted.
- **Monitoring:** Prometheus metrics and a `pipeline_runs` table (stage timings, how each field was decided).

### ai-gateway
- Endpoints: `field-read`, `classify`, `page-analysis`, `template-suggest`.
- Fair queuing across all gateway copies: priority work is served 3:1 over standard.
- Daily AI limits per account. A circuit breaker stops calls while the model server is down.
- **mock-model:** a stand-in model server that returns made-up answers. With it, every AI flow can be tested without
  the GPU server.

---

## 3. Contracts we implement

### Messages (RabbitMQ, IDP-00 ch. 7.6)

Every message uses the standard envelope (`messageId`, `type`, `version`, `occurredAt`, `producer`, `correlationId`,
`tenantId`, `subjectId`, `payload`). Every queue is a quorum queue with its own dead-letter queue and three retry
queues (10 s, 1 min, 10 min).

| Direction | Message | Exchange · routing key | Our queue |
|---|---|---|---|
| Java → conversion | `ConversionRequested` | `idp.commands` · `conversion.requested.priority` / `.standard` | `conversion-service.conversion-requested.priority` / `.standard` |
| Java → extraction | `ExtractionRequested` (also RECHECK) | `idp.commands` · `extraction.requested.priority` / `.standard` | `extraction-service.extraction-requested.priority` / `.standard` |
| Java → extraction | `DeepAnalysisRequested` | `idp.commands` · `extraction.deep-analysis.requested` | `extraction-service.deep-analysis-requested` |
| user-service → extraction | `AccountDeleted` | `idp.events` · `account.deleted` | `extraction-service.account-deleted` |
| Python → Java | `JobProgressed` | `idp.events` · `job.progressed` | Java binds its own queue |
| Python → Java | `ConversionCompleted` | `idp.events` · `conversion.completed` | Java binds its own queue |
| Python → Java | `ExtractionCompleted` | `idp.events` · `extraction.completed` | Java binds its own queue |
| Python → Java | `WorkFailed` | `idp.events` · `work.failed` | Java binds its own queue |

**How we behave with messages:**
- **Duplicates:** we recognise a repeated message by the AMQP header `eventId`, falling back to the envelope's
  `messageId`. A repeat is recorded once.
- **A request for an item that already finished:** we send the stored completion again; the work is not repeated.
- **Publishing:** events go out through an outbox. Every publish is persistent, mandatory and waits for a publisher
  confirm. **An event nobody has bound a queue to is not lost, but it blocks that item's later events** until a
  queue exists.

### HTTP API (extraction-service, through the Java gateway)

These routes need a Keycloak bearer token with `aud=idp-api`. The account always comes from the token's
`account_id`, and another account's template answers 404.

| Area | Routes |
|---|---|
| Document types | `GET`, `POST`, `PATCH /api/v1/document-types[/{code}]` |
| Templates | `GET`/`POST /api/v1/templates`, `GET /{id}`, `GET`/`PUT /{id}/versions/{n}`, `POST /{id}/versions`, `/publish`, `/restore`, `/clone`, `/import`, `/export`, `DELETE /{id}` |
| Designer | `POST`/`GET .../samples`, `POST .../suggestions`, `POST .../test-runs`, `GET .../test-runs/{runId}` |
| Mobile | `GET /api/v1/templates/bundle?since=` |
| Probes | `/health/live`, `/health/ready` (no token), `/metrics` |

Errors are RFC 9457 problem details with a `code`, for example `EXT-404-TEMPLATE-001`. The same JSON shape as the
Java services.

### Object storage (MinIO, IDP-00 ch. 7.7)

| Bucket | We read | We write |
|---|---|---|
| `idp-originals` | uploaded files | nothing |
| `idp-derived` | stored results and page images (recheck, deep analysis) | page images, `result.json`, previews: `<accountId>/<documentId>/<jobItemId>/…` |
| `idp-outputs` | nothing | conversion outputs: `<accountId>/<jobId>/<itemId>/…` |
| `idp-templates` | template samples | prepared samples: `<accountId or "standard">/<templateId>/<version>/samples/…` |

**Security rule:** every object key in a message must start with the message's `tenantId`. Anything else is refused
with `WorkFailed`.

---

## 4. What we need from the backend team

### For the first test session

| # | Item | Why |
|---|---|---|
| 1 | RabbitMQ, MinIO and Keycloak addresses for testing, **or** agreement that we run everything locally (section 6) | Messages only flow if both sides use the same broker and storage |
| 2 | The four buckets: `idp-originals`, `idp-derived`, `idp-outputs`, `idp-templates` | We read and write all four |
| 3 | **Queues bound to every event we publish:** `job.progressed`, `conversion.completed`, `extraction.completed`, `work.failed` | An event with no queue holds back that item's later events |
| 4 | Keycloak: the issuer URL and JWKS URL, and tokens with `aud=idp-api` and `account_id` | The extraction API checks every token |
| 5 | Keycloak service clients `extraction-service` and `conversion-service` (client credentials), audience `ai-gateway`, and their client secrets for us | ai-gateway accepts only these two services |
| 6 | One backend engineer to send test messages and read the results with us | Section 7 lists the scenarios |

### New error codes to add on the Java side

| Code | Meaning |
|---|---|
| `CNV-422-FILE-001`, `CNV-422-OPTIONS-001`, `CNV-422-TABLE-001`, `CNV-422-REQUEST-001`, `CNV-500-INTERNAL-001` | conversion: unreadable file, bad option, bad table, unusable request, internal failure (retryable) |
| `EXT-503-AI-001` | extraction: deep analysis or suggestions got no AI answer (retryable) |
| `EXT-409-SAMPLE-001`, `EXT-409-SAMPLE-002`, `EXT-404-TESTRUN-001` | extraction: template designer errors (HTTP) |

---

## 5. Contract points to agree

Each point has our current default, so testing can start before the answers come. A different answer is a small
change on our side.

| # | Service | Question | What we do now |
|---|---|---|---|
| 1 | extraction | **How does a RECHECK job arrive?** IDP-00 has no message type for it, and IDP-02 gives `options {resultId, fields}`, but we can't look up a result id | We accept `ExtractionRequested` with `options.recheck = {result: {bucket, key}, fields: [...]}` |
| 2 | extraction | `DeepAnalysisRequested` has no `documentId` | We read it from `resultKey` (`<accountId>/<documentId>/<itemId>/result.json`). **Please add `documentId`** |
| 3 | extraction | Designer samples: we can't look up document ids | We expect file keys: `samples[].files = [{bucket, key}]` |
| 4 | both | Is the `eventId` header the same value as the envelope's `messageId`? | We use `eventId`, fall back to `messageId`, and send both with the same value |
| 5 | both | Does a deliberate retry (after a retryable `WorkFailed`, or the 15-minute reaper) get a **new** `eventId`? | We assume yes. A redelivery keeps the same one |
| 6 | conversion | Where is the edit plan: `options.editPlan` or a top-level `editPlan`? | We accept both |
| 7 | conversion | Format of encrypted PDF passwords | Base64 of RSA-OAEP SHA-256 with our public key. We generate the key pair and share the public key |
| 8 | conversion | Export option names `layout`, `includeConfidence` | Not in any spec. Please confirm |
| 9 | conversion | Previews land in `idp-derived`; `target` may be left out for `PDF_MERGE` and `PREVIEW` | As stated |
| 10 | both | **Two fixes to the Java standard's outbox claim query** | Fixed in ours. Your relay uses the same query: (a) add `AND b.id <> o.id`, otherwise a retrying row blocks itself forever; (b) wrap it in a CTE and `ORDER BY created_at` outside, because `UPDATE … RETURNING` doesn't keep order |

---

## 6. How we will provide the services for local testing

### What we will hand over

One Docker Compose file for the whole Python stack. Started with one command, it brings up:

| Container | Purpose |
|---|---|
| `conversion-intake`, `conversion-relay`, `conversion-worker`, `conversion-health` | conversion-service |
| `extraction-api`, `extraction-intake`, `extraction-relay`, `extraction-worker`, `extraction-worker-ai` | extraction-service |
| `ai-gateway`, `mock-model` | the AI path, with made-up answers instead of the GPU server |
| `migrate` jobs | database migrations, plus the standard templates for extraction |
| PostgreSQL, Redis | our own databases and the gateway's queue and limits |
| RabbitMQ, MinIO, Keycloak | **only in mode B below** |

The extraction worker's OCR models (65 MB, Apache-2.0) are downloaded once by a script and checked against their
recorded checksums, so nothing has to be copied by hand.

### Two ways to run it (to decide together)

| | Mode A: join the backend's infrastructure (**recommended**) | Mode B: everything in our compose |
|---|---|---|
| RabbitMQ, MinIO, Keycloak | the backend's test instances | started by our compose |
| Java services | run as usual, unchanged | point at our RabbitMQ, MinIO and Keycloak |
| Closer to production | yes | less so |
| We need from you | the addresses and credentials (section 4) | nothing to start |

### Steps

1. We send the compose file, an `.env.example`, and a short README for starting, stopping and reading logs.
2. Together, we run the migrations and check `/health/ready` on every API.
3. Backend sends the scenarios in section 7, and we watch both sides. Every log line carries the request's
   `correlationId`, so one search follows a request across services.
4. We note each problem with the message that caused it, fix it, and run again.

Later, when the real model server and the scanned-document routes are ready: we change `MODEL__BASE_URL` from the
mock to the real server, and repeat the AI scenarios.

---

## 7. Scenarios for the first sessions

| # | Scenario | Expected |
|---|---|---|
| 1 | `ConversionRequested`: DOCX → PDF | `JobProgressed` …, then `ConversionCompleted`, with the output in `idp-outputs` |
| 2 | `ConversionRequested`: PDF_MERGE of two files | One merged PDF |
| 3 | `ConversionRequested`: PDF_EDIT with a redaction and a password | The edited PDF; the password decrypts (contract point 7) |
| 4 | `ConversionRequested`: EXPORT of an extraction result to XLSX | The XLSX in `idp-outputs` |
| 5 | `ExtractionRequested`: a PAN card image, `templateId: auto` | `ExtractionCompleted` with `documentType: PAN_CARD` and `result.json` in `idp-derived` |
| 6 | `ExtractionRequested`: a digital PDF and a DOCX | Read from the text layer, no OCR |
| 7 | `ExtractionRequested`: a blurred photo | `WorkFailed` with `EXT-422-QUALITY-001` |
| 8 | The same request twice (same `eventId`) | Processed once |
| 9 | The same item again after it finished (new `eventId`) | The stored `ExtractionCompleted` is sent again; no new work |
| 10 | RECHECK with `options.recheck` (contract point 1) | The result with `recheck.checked` and `recheck.changed` |
| 11 | `DeepAnalysisRequested` | `ExtractionCompleted` with the result's `analysis` section (from the mock model) |
| 12 | A key from another account in a request | `WorkFailed` with `EXT-400-VALIDATION-001` |
| 13 | Templates API through the Java gateway: list, create, publish | 200/201, problem details on errors, 404 for another account's template |
| 14 | `GET /api/v1/templates/bundle`, then again with `If-None-Match` | 200, then 304 |
| 15 | `AccountDeleted` | That account's templates archived |
| 16 | RabbitMQ stopped for a minute during a job | The job finishes after it comes back; nothing lost |

---

## 8. Known limits (written down in each service's ADRs)

- **Not tried yet against the real AI model server.** Every AI test used recorded answers.
- **conversion:** the 6 scanned-document routes, which need OCR and AI.
- **extraction:**
  - unknown document layouts and tables (F-EX-12);
  - OCR engine C;
  - the evaluation against labelled golden sets.
- **ai-gateway:** `table-repair`.
- **All three:**
  - OpenTelemetry tracing: planned next. Until then, logs carry the correlation id;
  - Eureka registration: please tell us whether Python services should register.

---

## 9. Next steps

1. **Backend:**
   - choose mode A or B, and send the addresses and credentials (section 4);
   - set up the queues and Keycloak clients;
   - answer the ten contract points (section 5).
2. **Python team:** deliver the compose file and README. We start as soon as mode A or B is chosen.
3. **Together:** a first session on scenarios 1, 5, 8 and 13 (one per area), then the rest.
