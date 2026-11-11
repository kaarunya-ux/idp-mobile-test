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
| `/internal/v1/ai/page-read` | `accountId, priority?, plan?, page` | `analysis{nBlocks, tableColumnCounts}, blocks[]`: headings, paragraphs, lists (text, align, size, bold) and tables (hasHeader, header, rows). For conversion-service's scanned routes |
| `/internal/v1/ai/generic-read` | `accountId, priority?, plan?, pages[1..2]`: a document's page images (a card's front and back are read as one) | `documentType` (a lower snake_case code, or null), `documentConfidence`, `fields[{key, label, type, value, confidence}]`; a `table` field's value is `{headers, rows}`. Any document, when no template matched it: extraction-service's synchronous path (ADR 0003) |

`priority` is `STANDARD` (the default), `PRIORITY` (served 3:1 over STANDARD) or `INTERACTIVE`: a person waiting on an
open request (extraction-service's synchronous path). INTERACTIVE goes before both, may use the slots the queued lanes
may not take (`QUEUE__INTERACTIVE_RESERVED_SLOTS`), and waits at most `QUEUE__INTERACTIVE_WAIT_TIMEOUT` before
`AIG-503-BUSY-001`, so the caller can send the work the queued way instead.

Images are `{bucket, key}` in MinIO, or `{data}`: the image itself, base64, for extraction's synchronous lane, which stores nothing (ADR 0004). Errors are RFC 9457 problem details:

| Code | Status | Meaning |
|---|---|---|
| `AIG-400-VALIDATION-001` | 400 | The body is wrong |
| `AIG-401-AUTH-001` / `AIG-403-AUTH-001` | 401 / 403 | No valid service token / a client that may not call |
| `AIG-422-INPUT-001` | 422 | An image is missing, too large, not an image, or in a bucket the gateway doesn't read |
| `AIG-429-CAP-001` | 429 | The account has used its AI calls for today |
| `AIG-502-OUTPUT-001` | 502 | The model's answer wasn't the schema, also after one repair attempt |
| `AIG-503-MODEL-001` | 503 | The model server is down (breaker open) |
| `AIG-503-BUSY-001` | 503 | No model slot within 20 s (5 s for INTERACTIVE) |
| `AIG-503-DEPENDENCY-001` | 503 | Redis or storage isn't configured or reachable |

Callers treat every 5xx as "AI unavailable": the fields stay for review, and the job never fails because of it.

## Configuration

| Variable | Default | |
|---|---|---|
| `SERVICE_NAME`, `ENVIRONMENT` | `ai-gateway`, `local` | |
| `LOGGING__LEVEL`, `LOGGING__FORMAT` | `INFO`, `json` | |
| `BUILD__COMMIT` | `unknown` | The git commit of the image; set it at deploy (the images do not set it yet). With `ENVIRONMENT`, the version and `config_hash` (the settings that change behaviour plus the hash of the prompt files) it is on every log line, and `ai_build_info{env,version,commit,config_hash}` is exported at `/metrics` (prompt drift strategies 1, 3 and 24) |
| `MODEL__BASE_URL` | `http://10.0.3.66:4000/v1` | OpenAI-style server |
| `MODEL__API_KEY` | none | Sent as a bearer token when set |
| `MODEL__VL_MODEL`, `MODEL__SMART_MODEL`, `MODEL__AUTO_MODEL` | `vl`, `smart`, `auto` | The names the server knows |
| `MODEL__VL_MAX_CONCURRENT` | 4 | The server's capacity, shared by all replicas |
| `MODEL__TIMEOUTS__FIELD_READ`, `__CLASSIFY`, `__PAGE_ANALYSIS`, `__TEMPLATE_SUGGEST`, `__PAGE_READ`, `__GENERIC_READ`, `__CONNECT` | 30 s, 30 s, 90 s, 90 s, 180 s, 40 s, 2 s | ISO 8601 durations such as `PT180S` (a bare number like `180` is refused) |
| `MODEL__PAGE_READ_MAX_SIDE_PX` | 2048 | A whole page is sent larger than a card, so small print stays readable |
| `MODEL__PAGE_READ_MIN_BLOCK_SHARE` | 0.5 | A page read is asked for once more, then refused (`AIG-502-OUTPUT-001`), when it writes fewer blocks than this share of the number its own analysis counted (ADR 0007) |
| `PROMPTS__PINS` | `{}` | JSON, for example `{"page-read": 2}`: fixes a prompt at an older version instead of the highest. **This is the rollback** when a new prompt version turns out worse; restart the gateway after setting it (ADR 0008) |
| `MODEL__MAX_IMAGE_SIDE_PX`, `MODEL__MAX_IMAGE_BYTES` | 1280, 20 MiB | |
| `MODEL__PAGE__BASE_URL`, `__MODEL`, `__SEND_SCHEMA`, `__API_KEY` | none, `page`, `true`, none | A separate model server for page-read (conversion's whole pages), e.g. the Qwen3.6-35B on llama-server; unset, page-read uses the main server's vision model (ADR 0005) |
| `MODEL__SEND_SCHEMA` | `true` | `false` leaves the JSON schema out of `response_format`: mlx_vlm.server's enforcement corrupts the first value. Answers are checked against the schema here either way |
| `MODEL__THINKING` | `false` | Hybrid models' reasoning when the prompt doesn't set it, sent as `chat_template_kwargs.enable_thinking` (llama.cpp) and `enable_thinking` (mlx_vlm.server); unset sends neither |
| `MODEL__MIN_IMAGE_SIDE_PX` | 768 | field-read and generic-read enlarge a smaller card picture (after trimming its white margin): tiny print is misread |
| `QUEUE__WAIT_TIMEOUT`, `QUEUE__PRIORITY_SHARE`, `QUEUE__LEASE` | 20 s, 3, 3 min | |
| `QUEUE__INTERACTIVE_RESERVED_SLOTS`, `QUEUE__INTERACTIVE_WAIT_TIMEOUT` | 1, 5 s | Slots only INTERACTIVE may use (lower than `MODEL__VL_MAX_CONCURRENT`), and its wait before busy |
| `CAPS__GUEST`, `CAPS__PERSONAL`, `CAPS__CORPORATE` | 10, 30, 5000 | Calls per account per UTC day |
| `BREAKER__FAILURES`, `BREAKER__WINDOW`, `BREAKER__RESET_AFTER` | 5, 30 s, 30 s | |
| `REDIS__URL` | none | Without it the AI routes answer 503 |
| `STORAGE__ENDPOINT`, `STORAGE__ACCESS_KEY`, `STORAGE__SECRET_KEY`, `STORAGE__REGION`, `STORAGE__ALLOWED_BUCKETS`, `STORAGE__TIMEOUT` | none, none, none, `us-east-1`, `["idp-originals","idp-derived","idp-templates"]`, 30 s | Without them the AI routes answer 503 |
| `AUTH__ISSUERS` | `[]` | JSON list of `{issuer, jwks_url}` |
| `AUTH__AUDIENCE`, `AUTH__ALLOWED_CLIENTS` | `ai-gateway`, `["extraction-service","conversion-service"]` | |

## Without a model server: `mock-model`

For laptops and CI (IDP-03 ch. 5). It answers `POST /v1/chat/completions` like the real server, with a recorded
answer when one matches (`src/mock_model/recorded/*.json`: schema name plus a phrase of the question; made-up values
only) and otherwise the smallest answer that fits the requested schema, so every task works.

```bash
make mock-model                                    # :4000
MODEL__BASE_URL=http://localhost:4000/v1 make run  # the gateway, answered by the mock
```

`MOCK_MODEL__LATENCY_MS` adds a delay, to see queueing and timeouts. Never deploy it: it is not a model.

## Data flow and personal data

The pictures sent here can show Aadhaar and PAN cards, names, addresses and dates of birth. Where that data goes
(DPDP Act 2023, the standard's OBS-04, LLM-06 and FIL-08):

| Step | What happens | Where it stays |
|---|---|---|
| In | A caller (extraction-service, conversion-service) sends an image: `{bucket, key}` in MinIO, or `{data}` inside the request (the synchronous lane and conversion's scanned pages) | Inside the request, or in the caller's private bucket |
| Resize | The picture is resized and tidied in memory | Memory, for the length of the call |
| Out | The picture and the prompt go to the model server: `MODEL__BASE_URL`, and `MODEL__PAGE__BASE_URL` for whole pages | The company's own model servers; no other host is called |
| Back | The model's JSON answer is validated and returned to the caller | The response; nothing is stored here |
| Logs | One line per call: task, prompt version, client, account id, outcome, latency, token counts | Never the picture, the prompt, the answer or a field value. A mask on every log line, tracebacks included, replaces Aadhaar-, PAN-, e-mail- and phone-shaped text as a safety net |
| Metrics | Labels are task, model and outcome | Never a value |
| Redis | Per-account daily counters and slot counters, keyed by account id and model, with an expiry | No document data |

Not verified from this repository: whether the model servers keep their own request logs or prompt caches of images.
That is a setting of `llama-server` or `mlx_vlm.server`, not of this service, and needs checking on each model host
before real documents are sent there.


**Which prompt is running, and when it changed.** At start-up the gateway logs one `prompt_loaded` line per prompt
(`prompt_version`, `prompt_hash`, `pinned`, temperature, max tokens) and a `prompts_loaded` summary, each with the build
stamp (`env`, `commit`, `config_hash`). Every `ai_call_finished` line carries the `prompt_version` and `prompt_hash` that
answered. A change of prompt therefore shows in the logs as a different version or hash at a later start-up. Whether
the *text* got worse is for the golden suites (`services/golden-tests/`); `PROMPTS__PINS` rolls a prompt back.


## Metrics

`/metrics`: `ai_requests_total{task,model,outcome}`, `ai_latency_seconds{task,model}`, `ai_queue_wait_seconds{priority}`,
`ai_tokens_total{model,direction}`, `ai_semaphore_in_use{model}`.

ADRs: [docs/adr](docs/adr). Build log: [docs/ai-gateway/build-log.md](../../docs/ai-gateway/build-log.md).
