# IDP Web Extraction App

The web side of the IDP: a FastAPI service that reads **PAN, Aadhaar and Indian driving licence** images and
PDFs with a vision-language model, plus a benchmark that finds how many users one model can serve at once.
Same design as the VMBS extraction services (image prep, one schema-constrained model call, JSON out, test
page).

Current setup: everything runs on one Mac mini (10.0.3.99, Apple Silicon), with **Qwen3.5-4B** (4-bit) on MLX.

```
browser / client ──▶ idp_web_app (:8020) ──HTTP──▶ mlx_vlm.server (:8082)  Qwen3.5-4B-MLX-4bit
                     same Mac                       same Mac
```

- Test page: http://10.0.3.99:8020/ (PAN / Aadhaar / Driving licence tabs, plus a "several users at once" test)
- Swagger: http://10.0.3.99:8020/docs

## Quick start (this Mac)

Both parts run from the repo root (`idp-mobile-test/`). Python 3.13.

**1. Model host, once:** an MLX venv and the model (2.9 GB).

```bash
python3 -m venv .venv-mlx
.venv-mlx/bin/pip install mlx==0.32.4 mlx-vlm==0.7.10 transformers==5.20.2
.venv-mlx/bin/hf download mlx-community/Qwen3.5-4B-MLX-4bit --local-dir models/Qwen3.5-4B-MLX-4bit
```

**2. Start the model** (port 8082; `caffeinate` keeps the Mac awake while it runs):

```bash
nohup ./idp_web_app/model_host/start_mlx.sh > idp_web_app/logs/mlx_model.log 2>&1 &
curl http://127.0.0.1:8082/v1/models          # lists models/Qwen3.5-4B-MLX-4bit when ready
```

**3. Start the app** (port 8020):

```bash
python3 -m venv .venv && .venv/bin/pip install -r idp_web_app/requirements.txt    # once
cp idp_web_app/.env.example idp_web_app/.env     # once; already points at the MLX model on :8082
mkdir -p idp_web_app/logs
cd idp_web_app && nohup ../.venv/bin/uvicorn app:app --host 0.0.0.0 --port 8020 >> logs/app.log 2>&1 &
curl http://127.0.0.1:8020/health             # {"status":"ok","model":"models/Qwen3.5-4B-MLX-4bit"}
```

(`./start.sh` does the same in the foreground, with the settings from `.env`.)

**Stop / restart:** stop each by its port, never by a name pattern (other services on this Mac run
uvicorn too): `kill $(lsof -tiTCP:8020 -sTCP:LISTEN)`, then start again as above. Same for 8082.

**MLX specifics** (all set in `.env.example`): `LLAMA_MODEL` must name the loaded model, and `SEND_SCHEMA=false`
because mlx_vlm.server's schema enforcement corrupts the first value; the app checks every value against the
schema itself. The app also turns off Qwen3.5's "thinking" mode on every request.

## API

| Endpoint | Form fields | Returns |
|---|---|---|
| `POST /api/v1/pan/extract` | `file` | `pan_number, name, parent_name, parent_relation, dob` |
| `POST /api/v1/aadhaar/extract` | `file`, optional `back` | `aadhaar_number, name, dob, gender, address, pincode` |
| `POST /api/v1/dl/extract` | `file`, optional `back` | `license_number, name, dob, address, issue_date, validity_nt, validity_tr, blood_group, organ_donor, vehicle_classes` |

- Body is `multipart/form-data`. `back` is the back side of the same card, sent in the same request;
  both sides are read together into one result.
- Files: **PDF**, JPEG, PNG or WebP, up to 15 MB (HEIC is rejected). A PDF's first page is read; for Aadhaar and
  DL, a PDF sent without `back` has its second page (if any) read as the back. Password-protected PDFs (such
  as e-Aadhaar downloads) are rejected: remove the password first.
- Every key is always present; `null` when not printed or not readable.
- Errors: `{"error": {"code": ..., "message": ...}}` (`INVALID_IMAGE` 400, `IMAGE_TOO_LARGE` 413,
  `INVALID_REQUEST` 422, `INTERNAL_ERROR` 500, `MODEL_ERROR` / `MODEL_OUTPUT_INVALID` 502,
  `MODEL_UNAVAILABLE` 503, `MODEL_TIMEOUT` 504).
- Test page at `/` (PAN / Aadhaar / Driving licence tabs), Swagger at `/docs`, `GET /health`.

## Settings (`.env`)

| Variable | Default | Meaning |
|---|---|---|
| `LLAMA_URL` | `http://127.0.0.1:8081` | Model host (`.env.example`: the MLX model on this Mac, `:8082`) |
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

## Alternative model host: llama-server (mini3)

Used during the model comparison. `model_host/start_model.sh` runs one model with llama-server. Each model needs its weights `.gguf`
and its `mmproj` `.gguf` (vision projector).

```bash
MODEL=/path/model.gguf MMPROJ=/path/mmproj.gguf ALIAS=qwen3-vl-4b NP=4 ./model_host/start_model.sh
```

- `NP` = parallel slots, the number of requests served at the same time. Context is `NP x CTX_PER_SLOT`
  (default 4096 per slot), so memory grows with `NP`.
- `ALIAS` is the name the benchmark files its results under; give each model a distinct one.
- `model_host/mem_watch.sh <file>.csv` logs llama-server memory, free memory and swap every 2 s.
- Point the app at it with `LLAMA_URL=http://<host>:8081`, `LLAMA_MODEL=` (empty) and `SEND_SCHEMA=true`.

## Concurrency benchmark

Test images and their correct values are in `bench/ground_truth.json` (local only, gitignored: real ID
data). For each model, one at a time:

1. Start the model host (MLX: `model_host/start_mlx.sh` with `SEQS` = the highest level to test; llama-server:
   `start_model.sh` with `NP` = that level, plus `mem_watch.sh`).
2. Here: `../.venv/bin/python bench/bench.py --levels 1,2,4,8 --rounds 2`
   (the app must be running and pointing at that model host). Every request carries a copy of its test image with
   the brightness shifted by a level or two, so the model host never sees the same image twice and can
   reuse only the cached prompt text, as with real users.
3. Stop the model, start the next one, repeat.
4. `../.venv/bin/python bench/compare.py --md bench/results/compare.md` for the side-by-side table.

Per level the benchmark reports median / 95th-percentile latency, documents per minute, field accuracy
and errors (timeouts, context full). Addresses count as correct at >= 90% character similarity; other
fields must match exactly, ignoring case, spaces and punctuation. Results store only right/wrong per
field, never the extracted values.

Models tested (findings in this folder, each as `.md` and `.pdf`):

| Findings | Model / engine |
|---|---|
| `LLAMACPP_VS_MLX.md` | Qwen3.5-4B on llama.cpp vs MLX (the model now in use, on MLX) |
| `QWEN2.5_VL_3B_MLX.md` | Qwen2.5-VL-3B, MLX |
| `QWEN3_VL_8B_MLX.md` | Qwen3-VL-8B, MLX |
| `QWEN3.5_9B_MLX.md` | Qwen3.5-9B, MLX |
| `QWEN3.6_35B_A3B_LLAMACPP.md` | Qwen3.6-35B-A3B (MoE, 3B active), llama.cpp |
| `QWEN3.6_REAP_19B_OPTIQ.md` | Qwen3.6-35B-A3B REAP-19B, OptiQ |

Prompts were written and tuned on Qwen3.5-4B.

## Files

```
app.py            API, Swagger models, error handling
extract.py        image / PDF prep (render PDF pages, trim margin, stack front + back, resize) and the model call
doc_schemas.py    fields, prompts and JSON schemas per document type
llm.py            model host client (based on the VMBS one)
static/           test page
start.sh          start the app in the foreground with the settings from .env
model_host/       start_mlx.sh (MLX model on this Mac); start_model.sh, mem_watch.sh (llama-server, mini3)
bench/            bench.py, compare.py, ground_truth.json (local only)
*.md / *.pdf      model test findings (see the table above)
```
