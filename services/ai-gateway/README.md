# ai-gateway

The only component that talks to the local model server (IDP-03 ch. 4). extraction-service and conversion-service call
it with a service token; it owns the prompts, answer schemas, model choice, fair queuing, daily caps, timeouts,
retries and usage reporting. Internal only, port 8002.

## Run

```bash
cp .env.example .env      # then fill in local values
make sync
make run                  # http://127.0.0.1:8002/docs
make check                # lint, types, import rules, tests (the integration tests need Docker)
```

## API

All `POST`, JSON, `Authorization: Bearer <service token>`. Every answer also has `model`, `promptVersion`,
`latencyMs` and `tokens: {in, out}`.

| Path | Body | Answer |
|---|---|---|
| `/internal/v1/ai/field-read` | `accountId, priority?, plan?, documentType?, pages[1..2], fields[{key, label?, type?, rule?, readings?}]`, or `crop, field, readings` | `fields[{key, value}]`, or `value, confidence, reason` |
| `/internal/v1/ai/classify` | `accountId, priority?, plan?, page, candidates[]` | `documentType, confidence` |
| `/internal/v1/ai/page-analysis` | `accountId, priority?, plan?, pages[1..5], currentResult, question?` | `fields[], summary, warnings[]` |
| `/internal/v1/ai/template-suggest` | `accountId, priority?, plan?, page, ocrWords[{text, box}]` | `fields[], tables[]` |

Images are `{bucket, key}` in MinIO: callers never send bytes. Errors are RFC 9457 problem details:

| Code | Status | Meaning |
|---|---|---|
| `AIG-400-VALIDATION-001` | 400 | The body is wrong |
| `AIG-401-AUTH-001` / `AIG-403-AUTH-001` | 401 / 403 | No valid service token / a client that may not call |
| `AIG-422-INPUT-001` | 422 | An image is missing, too large, not an image, or in a bucket the gateway doesn't read |
| `AIG-429-CAP-001` | 429 | The account has used its AI calls for today |
| `AIG-502-OUTPUT-001` | 502 | The model's answer wasn't the schema, also after one repair attempt |
| `AIG-503-MODEL-001` | 503 | The model server is down (breaker open) |
| `AIG-503-BUSY-001` | 503 | No model slot within 20 s |
| `AIG-503-DEPENDENCY-001` | 503 | Redis or storage isn't configured or reachable |

Callers treat every 5xx as "AI unavailable": the fields stay for review, and the job never fails because of it.

## Configuration

| Variable | Default | |
|---|---|---|
| `SERVICE_NAME`, `ENVIRONMENT` | `ai-gateway`, `local` | |
| `LOGGING__LEVEL`, `LOGGING__FORMAT` | `INFO`, `json` | |
| `MODEL__BASE_URL` | `http://10.0.3.66:4000/v1` | OpenAI-style server |
| `MODEL__API_KEY` | none | Sent as a bearer token when set |
| `MODEL__VL_MODEL`, `MODEL__SMART_MODEL`, `MODEL__AUTO_MODEL` | `vl`, `smart`, `auto` | The names the server knows |
| `MODEL__VL_MAX_CONCURRENT` | 4 | The server's capacity, shared by all replicas |
| `MODEL__TIMEOUTS__FIELD_READ`, `__CLASSIFY`, `__PAGE_ANALYSIS`, `__TEMPLATE_SUGGEST`, `__CONNECT` | 30 s, 30 s, 90 s, 90 s, 2 s | ISO 8601 durations or seconds |
| `MODEL__MAX_IMAGE_SIDE_PX`, `MODEL__MAX_IMAGE_BYTES` | 1280, 20 MiB | |
| `QUEUE__WAIT_TIMEOUT`, `QUEUE__PRIORITY_SHARE`, `QUEUE__LEASE` | 20 s, 3, 3 min | |
| `CAPS__GUEST`, `CAPS__PERSONAL`, `CAPS__CORPORATE` | 10, 30, 5000 | Calls per account per UTC day |
| `BREAKER__FAILURES`, `BREAKER__WINDOW`, `BREAKER__RESET_AFTER` | 5, 30 s, 30 s | |
| `REDIS__URL` | none | Without it the AI routes answer 503 |
| `STORAGE__ENDPOINT`, `STORAGE__ACCESS_KEY`, `STORAGE__SECRET_KEY`, `STORAGE__REGION`, `STORAGE__ALLOWED_BUCKETS`, `STORAGE__TIMEOUT` | none, none, none, `us-east-1`, `["idp-originals","idp-derived"]`, 30 s | Without them the AI routes answer 503 |
| `AUTH__ISSUERS` | `[]` | JSON list of `{issuer, jwks_url}` |
| `AUTH__AUDIENCE`, `AUTH__ALLOWED_CLIENTS` | `ai-gateway`, `["extraction-service","conversion-service"]` | |

## Prompts

`src/ai_gateway/prompts/<task>.v<n>.md`, with front matter (`name`, `version`, `model`, `temperature`, `max_tokens`).
The highest version is used. Changing a prompt means a new file plus a run on the golden set (LLM-02, ML-12). The
standard card types' questions are in `ai/domain/card_profiles.py`: change them together with a new
`field-read` version.

## Metrics

`/metrics`: `ai_requests_total{task,model,outcome}`, `ai_latency_seconds{task,model}`, `ai_queue_wait_seconds{priority}`,
`ai_tokens_total{model,direction}`, `ai_semaphore_in_use{model}`.

ADRs: [docs/adr](docs/adr). Build log: [docs/ai-gateway/build-log.md](../../docs/ai-gateway/build-log.md).
