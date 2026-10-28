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
