# IDP Web Extraction App

The web side of the IDP: a FastAPI service that reads **PAN, Aadhaar and Indian driving licence** images
with a vision-language model on a separate model host, plus a benchmark that finds how many users one
model can serve at once. Same design as the VMBS extraction services (image prep, one schema-constrained
model call, JSON out, test page).

```
browser / client ──▶ idp_web_app (:8020) ──HTTP──▶ model host (mini3, 10.0.3.66:8081)  llama-server + model
```

## API

| Endpoint | Form fields | Returns |
|---|---|---|
| `POST /api/v1/pan/extract` | `file` | `pan_number, name, parent_name, parent_relation, dob` |
| `POST /api/v1/aadhaar/extract` | `file`, optional `back` | `aadhaar_number, name, dob, gender, address, pincode` |
| `POST /api/v1/dl/extract` | `file`, optional `back` | `license_number, name, dob, address, issue_date, validity_nt, validity_tr, blood_group, organ_donor, vehicle_classes` |

- Body is `multipart/form-data`. `back` is the back side of the same card, sent in the same request;
  both sides are read together into one result.
- Every key is always present; `null` when not printed or not readable.
- Errors: `{"error": {"code": ..., "message": ...}}` (`INVALID_IMAGE` 400, `IMAGE_TOO_LARGE` 413,
  `INVALID_REQUEST` 422, `INTERNAL_ERROR` 500, `MODEL_ERROR` / `MODEL_OUTPUT_INVALID` 502,
  `MODEL_UNAVAILABLE` 503, `MODEL_TIMEOUT` 504).
- Test page at `/` (PAN / Aadhaar / Driving licence tabs), Swagger at `/docs`, `GET /health`.

## Run the app (this Mac)

```bash
cd idp_web_app
cp .env.example .env          # LLAMA_URL = the model host
../.venv/bin/pip install -r requirements.txt    # once, if the venv lacks them
./start.sh                    # serves on 0.0.0.0:8020
```

| Variable | Default | Meaning |
|---|---|---|
| `LLAMA_URL` | `http://127.0.0.1:8081` | Model host (`.env.example`: mini3) |
| `LLAMA_API_KEY` | empty | Must match the model host's key |
| `LLAMA_MODEL` | empty | Model id sent with requests; needed for mlx_vlm.server, not llama-server |
| `SEND_SCHEMA` | true | Model host enforces the output schema; `false` for mlx_vlm.server (its enforcement breaks the first value). The app checks every value against the schema either way |
| `HOST`, `PORT` | `0.0.0.0`, 8020 | Where the app listens |
| `LLAMA_TIMEOUT_S` | 120 | Give up on the model after this long |
| `EXTRACT_EDGE` | 1280 | Longest image side sent to the model |
| `MAX_UPLOAD_MB` | 15 | Largest accepted image |
| `MIN_EDGE` | 768 | Smaller images are enlarged to this (tiny print is misread) |
| `IMAGE_SCALE` | 1 | Final scale on every image; 0.875 gives a Qwen2.5-VL model (28 px pieces) the same token count as a Qwen3.5 model (32 px) |
| `CACHE_PROMPT` | true | Reuse the cached prompt text across requests; `false` only to measure without it |

## Model host (mini3)

`model_host/start_model.sh` runs one model with llama-server. Each model needs its weights `.gguf`
and its `mmproj` `.gguf` (vision projector).

```bash
MODEL=/path/model.gguf MMPROJ=/path/mmproj.gguf ALIAS=qwen3-vl-4b NP=4 ./model_host/start_model.sh
```

- `NP` = parallel slots, the number of requests served at the same time. Context is `NP x CTX_PER_SLOT`
  (default 4096 per slot), so memory grows with `NP`.
- `ALIAS` is the name the benchmark files its results under; give each model a distinct one.
- `model_host/mem_watch.sh <file>.csv` logs llama-server memory, free memory and swap every 2 s.

## Concurrency benchmark

Test images and their correct values are in `bench/ground_truth.json` (local only, gitignored: real ID
data). For each model, one at a time:

1. On mini3: start the model with `NP` = the highest level to test, and start `mem_watch.sh`.
2. Here: `../.venv/bin/python bench/bench.py --levels 1,2,4,8 --rounds 2`
   (the app must be running and pointing at mini3). Every request carries a copy of its test image with
   the brightness shifted by a level or two, so the model host never sees the same image twice and can
   reuse only the cached prompt text, as with real users.
3. Stop the model, start the next one, repeat.
4. `../.venv/bin/python bench/compare.py --md bench/results/compare.md` for the side-by-side table.

Per level the benchmark reports median / 95th-percentile latency, documents per minute, field accuracy
and errors (timeouts, context full). Addresses count as correct at >= 90% character similarity; other
fields must match exactly, ignoring case, spaces and punctuation. Results store only right/wrong per
field, never the extracted values.

Models to compare: Qwen3-VL-4B, Qwen3-VL-8B, Qwen2.5-VL-3B, Qwen3.6-35B-A3B (MoE, 3B active).

## Files

```
app.py            API, Swagger models, error handling
extract.py        image prep (trim margin, stack front + back, resize) and the model call
doc_schemas.py    fields, prompts and JSON schemas per document type
llm.py            model host client (based on the VMBS one)
static/           test page
model_host/       start_model.sh, mem_watch.sh (run on mini3)
bench/            bench.py, compare.py, ground_truth.json (local)
```
