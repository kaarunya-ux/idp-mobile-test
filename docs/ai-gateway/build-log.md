# ai-gateway build log

What was built, why, which rules it follows, and the commands to check it. Newest entry last.

## Entry 1: The gateway, and extraction-service's AI fallback on it (2026-10-28)

**Goal:** IDP-03 ch. 4. One service talks to the model server (`http://10.0.3.66:4000/v1`), with prompts, schemas,
fair queuing, caps, a breaker and usage reporting in one place. extraction-service's AI fallback is its first caller.

### What we did

```
services/ai-gateway/src/ai_gateway/
  core/          clock, ids, logging, errors, problem_details (copied from extraction-service)
                 error_codes.py (AIG-*), config.py, security.py (service tokens only), redis.py
  ai/domain/     models.py, exceptions.py, ports.py
                 card_profiles.py   the PAN, Aadhaar and DL questions (web app -> extraction -> here)
                 answers.py         JSON out of a fenced or chatty answer; card values checked per field
                 prompt_text.py     document text inside delimited data blocks (LLM-04)
  ai/application/runner.py          cap -> slot -> call -> check -> one repair -> AIG-502
                 answer_models.py   Pydantic shapes of every answer, and their JSON schemas
                 services/          read_fields, classify_page, analyse_page, suggest_template
  ai/infrastructure/
                 model_client.py    OpenAI-style /chat/completions with response_format json_schema
                 breaker.py         5 failures in 30 s -> open; one probe after 30 s
                 redis_slots.py     shared semaphore per model, PRIORITY 3:1 over STANDARD, Lua, 20 s wait
                 redis_caps.py      calls per account per UTC day
                 s3_images.py       images by key, long side <= 1280 px, a card's two sides stacked
                 prompt_files.py, metrics.py
  ai/api/        schemas, mappers, routes: POST /internal/v1/ai/{field-read, classify, page-analysis, template-suggest}
  prompts/       field-read.v1.md, classify.v1.md, page-analysis.v1.md, template-suggest.v1.md, repair.v1.md
  bootstrap.py, main.py, health.py (/health/ready pings Redis), /metrics
```

On the extraction side: `GatewayFieldReader`, `core/service_auth.py` (client-credentials token), pages stored before
the AI step, the priority route kept on the request (extraction ADR 0006, build log Entry 5).

### Why it is built this way

| Choice | Why |
|---|---|
| Batch `field-read` (all unsettled fields of a document in one call) beside the single crop form | One call per document instead of one per field; the web app's benchmarked way (ADR 0002) |
| Semaphore in Redis, not per process | The model server's capacity is shared by every gateway replica (IDP-03 ch. 4.5) |
| One Lua script per poll | Granting, fairness and clean-up of crashed holders happen atomically |
| Caps fail open, the semaphore fails closed | RDS-07: a rate limit vs. a lock. The semaphore is what protects the model server |
| A call that never reached the model is refunded | Busy or down shouldn't use up an account's allowance |
| Images by key, never bytes | IDP-03 ch. 4.3; the gateway alone decides the resize |
| Callers treat every 5xx as "AI unavailable" | The pipeline degrades to NEEDS_REVIEW, never fails (IDP-03 ch. 4.5) |

### Rules followed

LLM-01 to LLM-06 (prompts as versioned files, schema-constrained answers validated with Pydantic, data delimited,
timeouts/retries/limits/caps, no prompts or content in logs) · SEC-01, SEC-07 (service tokens, `aud=ai-gateway`,
allowed clients) · RDS-01, RDS-02, RDS-07 · ERR-04 (problem details) · ASY-01 (boto3 and Pillow in a thread) ·
DI-01 (clients built once) · TST-02, TST-04 (testcontainers Redis, respx) · DEP-02 (PyJWT: ADR 0001).

### Not built yet

`table-repair` (nothing reads tables yet) · Eureka registration · OpenTelemetry · a `mock-model` container for laptops
(IDP-03 ch. 5) · the golden-set evaluation of the prompts (ML-12).

### Check it

```bash
cd services/ai-gateway
uv sync
make check            # ruff, mypy --strict, import contracts, tests with coverage; integration tests need Docker
uv run pytest tests/integration -m integration     # Redis semaphore fairness, leases, caps
```

Result (`make check` on the dev machine, with Docker): 85 tests pass (78 unit, 7 Redis integration), 97.5% coverage.

## Entry 2: `mock-model`, the stand-in model server (2026-10-28)

**Goal:** run the whole AI path (extraction -> gateway -> model) on a laptop or in CI without the GPU server
(IDP-03 ch. 5), until the real model is pointed in.

### What we did

`src/mock_model/` (its own package; an import contract keeps it independent of `ai_gateway`): an OpenAI-style
`POST /v1/chat/completions`. A recorded answer when one matches the schema name and a phrase of the question
(`recorded/*.json`, made-up values only), otherwise the smallest answer that fits the requested JSON schema.
`make mock-model` serves it on :4000; `docker build --target mock-model` builds its image.

### Check it

```bash
cd services/ai-gateway
make mock-model                                    # one terminal
MODEL__BASE_URL=http://localhost:4000/v1 make run  # another: the gateway, answered by the mock
make check
```

Result (`make check` on the dev machine, with Docker): 92 tests pass (85 unit, 7 for the mock among them: every
gateway task passes its own answer checks against it; 7 Redis integration), 97.2% coverage.

## Entry 3: `page-read`, for conversion-service's scanned routes (2026-11-02)

A whole page picture as blocks. The prompt and the answer's shape are Sumukh's model-first reading (PR #20,
`services/conversions/prompts/read_page.v1.md`, copied as the system prompt): an analysis first (block count, column
counts), then headings, paragraphs, lists and tables with how each looks. Two additions to the gateway:

- **`thinking` in a prompt's front matter**, sent on every call as `chat_template_kwargs.enable_thinking`: a server
  restarted with thinking on had spent the whole output budget reasoning (Sumukh's finding).
- **A larger picture for whole pages** (`MODEL__PAGE_READ_MAX_SIDE_PX`, 2048) than for cards (1280), and its own
  timeout (`MODEL__TIMEOUTS__PAGE_READ`, 180 s).

The mock model has a recorded page (made-up text). Result here: 89 unit tests pass.

## Entry 4: The INTERACTIVE lane and `generic-read`, for extraction's synchronous path (2026-11-03)

**Goal:** the backend calls extraction-service directly for one document and waits for the answer (extraction ADR
0009). The model must serve that person first, and read any document without a template. Decisions: ADR 0003.

### What we did

- **A third priority, `INTERACTIVE`.** The semaphore's Lua script got a third waiting queue, served before PRIORITY
  and STANDARD, and a second holder set for the queued lanes: together they may hold at most
  `MODEL__VL_MAX_CONCURRENT - QUEUE__INTERACTIVE_RESERVED_SLOTS` slots (default 3 of 4), so one is always free for a
  person. INTERACTIVE waits only `QUEUE__INTERACTIVE_WAIT_TIMEOUT` (5 s) before `AIG-503-BUSY-001`. Settings refuse
  reserving every slot.
- **`POST /internal/v1/ai/generic-read`**, prompt `generic-read.v1.md` (thinking off): the document type as a
  lower snake_case code and every field `{key, label, type, value, confidence}`, tables as `{headers, rows}`, Hindi
  kept in Devanagari. The answer is tidied: one field per key (front and back are one document), no confidence
  without a value. The mock model has a made-up recorded answer.
- **The image store**: photos are turned upright from EXIF; `tidy` trims the white margin and enlarges a picture
  under `MODEL__MIN_IMAGE_SIDE_PX` (768), from the web extraction app (`feature/visiting_card_extraction`).
  field-read (both forms: a single field's crop is small) and generic-read use it.

### Check it

```bash
cd services/ai-gateway && make check
```

Result here (no Docker): ruff, mypy and import contracts clean; 105 unit tests pass, 95% coverage. The semaphore's
integration tests (11, four new for the lane) passed against an in-memory Redis with Lua; they still need the real
Redis run (`make check` with Docker).

## Entry 5: The Mac mini's model as the model server (2026-11-03)

**Goal:** real AI answers in the local stack when the backend tests, from the Qwen3.5-4B model the Mac mini already
serves (`http://10.0.3.99:8082/v1`, mlx_vlm.server), while the Mac mini's own web app keeps running there.

### What we did

- **`MODEL__SEND_SCHEMA`** (default true): false leaves `response_format` out, because mlx_vlm.server's schema
  enforcement corrupts the first value (found by the web extraction app). Every answer is still validated here, with
  one repair attempt.
- **`MODEL__THINKING`** (default false): thinking off for every task whose prompt doesn't say, sent in both servers'
  forms (`chat_template_kwargs.enable_thinking` for llama.cpp, `enable_thinking` for mlx_vlm.server). Qwen3.5 is a
  hybrid model: left on, it reasons before answering and spends the output budget.
- The local stack: `MODEL_NAME` maps all three model roles to the one model; `make use-mac-model` /
  `make use-mock-model` switch ai-gateway alone.

### Check it

Against the Mac mini's model, with a made-up PAN card, through the gateway's own services (no Docker):

| Task | Result | Time |
|---|---|---|
| field-read, whole card | all 4 fields right | 6.3 s |
| field-read, one field | right | 4.5 s |
| generic-read | `pan`, 6 fields, all right | 13.8 s |
| classify | **wrong**: answers by the candidates' order, not the image (it names the card right, then picks another) | 3.9 s |

classify needs a different question for this model: the type in its own words, matched to the candidates in code.

## Entry 6: Images inside the request (2026-11-04)

`ImageRefBody` takes `{bucket, key}` or `{data}` (base64), exactly one; the image store reads `data` with the same
checks as a stored image (ADR 0004). For extraction's synchronous lane, which keeps nothing in storage.
