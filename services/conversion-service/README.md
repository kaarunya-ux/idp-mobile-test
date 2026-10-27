# conversion-service

Converts documents between PDF, Word, Excel, CSV, PowerPoint and images. Also applies PDF edit plans, merges PDFs, exports extraction results and renders page previews. Specified in IDP-03 ch. 3 and built to the Python Engineering Standard.

There is **no public API**. document-service (Java) sends `ConversionRequested` messages over RabbitMQ, and this service replies with `JobProgressed`, `ConversionCompleted` or `WorkFailed` (IDP-00 ch. 7.6). Files go in and out through MinIO; messages carry only their locations.

**Status:** step 5 of the [build log](../../docs/conversion-service/build-log.md): every job type in IDP-00 ch. 7.5 runs. It receives `ConversionRequested` from RabbitMQ, works in Celery workers, reads and writes files in MinIO, and replies through an outbox in its own database (`conversion_db`).

| Job type | What it does |
|---|---|
| `CONVERSION` | 36 of the 47 routes in the IDP-00 matrix; the 11 that read scanned pages need OCR and fail with `CNV-422-ROUTE-001` for now |
| `PDF_MERGE` | PDFs and images, in order, into one `merged.pdf` |
| `PREVIEW` | a JPEG thumbnail per page, written to `idp-derived` |
| `PDF_EDIT` | the IDP-03 ch. 3.5 edit plan: reorder, rotate, delete, blank and borrowed pages; text, highlight, ink, signature and image overlays; watermark, page numbers; true redaction; form filling; text replacement; metadata; password protection; compression; splitting |
| `EXPORT` | extraction results as JSON, CSV, XLSX, DOCX, PDF or XML; masked fields stay masked |

## How a request flows

```
Java ──ConversionRequested──▶ idp.commands ──▶ intake ──▶ conversion_db (inbox + item, one transaction)
                                                  └──▶ Celery queue (office | pdf | data | ocr)
worker ──▶ MinIO (download input, upload outputs) ──▶ conversion_db (item + outbox rows, one transaction)
relay  ──▶ idp.events: JobProgressed, ConversionCompleted or WorkFailed (with the eventId header Java dedupes on)
```

A repeated message (same `eventId`) is recognised by the inbox table and not converted twice. A completed item asked for again gets its stored result back.

## System dependencies

The workers need these **system packages**. They aren't Python packages, so they aren't in `pyproject.toml`; the `worker` Docker image installs them.

| Package | Why |
|---|---|
| `libreoffice-writer`, `libreoffice-calc`, `libreoffice-impress` | Word, Excel and PowerPoint → PDF |
| `fonts-noto-core`, `fonts-noto-extra` | Fonts for Devanagari (Hindi) and other scripts, so text never turns into empty boxes (IDP-03 ch. 3.4) |

```bash
sudo apt install -y libreoffice-writer libreoffice-calc libreoffice-impress fonts-noto-core fonts-noto-extra
```

Without LibreOffice, the Office tests are skipped with a clear reason; everything else still runs.

## Run it

```bash
uv sync --all-extras   # create .venv from uv.lock, with dev tools and the worker libraries
make check             # format, lint, types, architecture rules, all tests: exactly what CI runs (needs Docker)
make check-unit        # the same without integration tests, when Docker isn't available
make run               # health server on http://127.0.0.1:8001/health/live
make up                # the whole stack in Docker: PostgreSQL, RabbitMQ, MinIO, intake, relay, worker
make migrate           # apply database migrations to DB__URL
```

**Try any job by hand:** with `make up` running, `uv run python scripts/send_job.py report.docx --to PDF` plays Java: it uploads the file, sends the job, prints every event and saves the outputs. `--type PDF_MERGE | PREVIEW | PDF_EDIT --plan plan.json | EXPORT --to XLSX` for the other job types.

Integration tests (`tests/integration/`) start PostgreSQL, RabbitMQ and MinIO in containers, so they need Docker. One of them runs the whole path: a message in, a real Celery worker converting a file from MinIO, and `ConversionCompleted` out on `idp.events`.

## Processes

| Process | Command | Image |
|---|---|---|
| health HTTP (probes only) | `uvicorn conversion.health:app --port 8001` | `health` |
| `intake` (FastStream consumer) | `faststream run conversion.consumers:create_app --factory` | `service` |
| `relay` (outbox publisher) | `python -m conversion.outbox_relay` | `service` |
| migrations (pre-deploy job) | `alembic upgrade head` | `service` |
| `worker-office` / `-pdf` / `-data` (Celery) | `celery -A conversion.worker worker -Q <queue> --prefetch-multiplier 1` | `worker` |

The last column is the Dockerfile target (`docker build --target <name>`).

## Environment variables

Read once at startup by `conversion.core.config.Settings`; an invalid value stops the process. Nested groups use `__`.

| Variable | Default | Allowed values | Purpose |
|---|---|---|---|
| `SERVICE_NAME` | `conversion-service` | 1–64 characters | Name in logs and traces |
| `ENVIRONMENT` | `local` | `local`, `dev`, `test`, `staging`, `prod` | Where the process runs |
| `LOGGING__LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` | Lowest level written |
| `LOGGING__FORMAT` | `json` | `json`, `console` | `console` for readable local logs; JSON everywhere else |
| `LIMITS__MAX_PAGES` | `1000` | 1–10,000 | PDFs with more pages are refused (`CNV-422-LIMIT-001`) |
| `LIMITS__MAX_IMAGE_PIXELS` | `180000000` | 1M–1G | Images with more pixels are refused, which stops decompression bombs |
| `LIMITS__MAX_RENDER_SIDE_PX` | `5000` | 500–20,000 | Rendered pages are scaled down so the longest side fits |
| `LIMITS__MAX_DOCUMENT_TABLE_ROWS` | `20000` | 100–1,000,000 | Tables longer than this aren't written into Word or slides; XLSX/CSV suit them |
| `OFFICE__BINARY` | `soffice` | a command or absolute path | The LibreOffice program used for Office → PDF |
| `OFFICE__TIMEOUT` | `60` | 5 s – 10 min (seconds, or an ISO 8601 duration like `PT90S`) | A run past this is stopped (`CNV-504-TIMEOUT-001`) |
| `DB__URL` | — (required by intake, relay, workers) | `postgresql+psycopg://user:password@host:5432/conversion_db` | The service's own database: inbox, outbox, item state |
| `DB__POOL_SIZE` / `DB__MAX_OVERFLOW` | `5` / `2` | 1–50 / 0–50 | Connections per process |
| `DB__STATEMENT_TIMEOUT` / `DB__LOCK_TIMEOUT` / `DB__IDLE_IN_TRANSACTION_TIMEOUT` | `5` / `3` / `10` (seconds) | ≥ 0.1 s / ≥ 0.1 s / ≥ 1 s | PostgreSQL stops anything slower (DB-02) |
| `RABBIT__URL` | — (required by intake, relay) | `amqp://user:password@host:5672/vhost` | Where `ConversionRequested` arrives and events are published |
| `RABBIT__PREFETCH` | `10` | 1–200 | Messages the intake holds at once |
| `RABBIT__DELIVERY_LIMIT` | `5` | 1–20 | Deliveries before a message goes to the dead-letter queue |
| `CELERY__BROKER_URL` | — (required by intake, workers) | `amqp://...` | The task queue between intake and workers |
| `CELERY__SOFT_TIME_LIMIT` / `CELERY__TIME_LIMIT` | `540` / `600` (seconds) | 30 s – 1 h | Soft: the item fails cleanly (`CNV-504-TIMEOUT-001`); hard: the task is killed |
| `CELERY__MAX_RETRIES` | `3` | 0–10 | Retries for passing failures (database, storage) before `CNV-500-INTERNAL-001` |
| `STORAGE__ENDPOINT` | — (required by workers) | `http://minio:9000` | MinIO, run by the Java team |
| `STORAGE__ACCESS_KEY` / `STORAGE__SECRET_KEY` | — (required by workers) | | This service's own MinIO keys |
| `STORAGE__REGION` | `us-east-1` | | S3 region name MinIO answers to |
| `STORAGE__BUCKET_OUTPUTS` | `idp-outputs` | a bucket name | Where converted and merged files are written |
| `STORAGE__BUCKET_DERIVED` | `idp-derived` | a bucket name | Where `PREVIEW` thumbnails are written (IDP-00 ch. 7.7) |
| `STORAGE__MAX_INPUT_SIZE` | `200MiB` | a size like `200MiB` or a byte count | Bigger inputs are refused before downloading (`CNV-422-LIMIT-001`) |
| `STORAGE__TIMEOUT` | `30` (seconds) | 1 s – 5 min | Connect and read timeout for MinIO |
| `EDIT__REDACTION_DPI` | `200` | 96–400 | How sharp a redacted page is when it is redrawn as a picture |
| `EDIT__MAX_IMAGE_BYTES` | `5MiB` | a size | Largest signature or inserted image |
| `CRYPTO__PRIVATE_KEY` | — (none) | RSA private key, PEM | Decrypts edit-plan passwords Java encrypted with our public key. Without it, plans with passwords fail with `CNV-422-PLAN-001` |
| `PREVIEW__MAX_SIDE_PX` | `400` | 64–2000 | Longest side of a page thumbnail; an A4 page comes out 283 × 400 |
| `PREVIEW__JPEG_QUALITY` | `75` | 30–95 | JPEG quality of thumbnails |
| `OUTBOX__BATCH_SIZE` | `100` | 1–1000 | Rows the relay claims at once |
| `OUTBOX__POLL_INTERVAL` | `1` (second) | 50 ms – 1 min | How long the relay waits when there is nothing to send |
| `OUTBOX__LEASE` | `60` (seconds) | ≥ 5 s | A claim older than this (its relay died) is given back |
| `OUTBOX__MAX_ATTEMPTS` | `10` | 1–100 | Failed publishes before a row is `DEAD` (logged as an error for alerting) |
| `OUTBOX__BACKOFF_MAX` | `300` (seconds) | ≥ 1 s | Longest wait between publish retries |

For local runs, copy `.env.example` to `.env`.

## Layout

```
src/conversion/
  health.py        probe endpoints (/health/live, /health/ready)
  bootstrap.py     composition root: builds the services and converters once per process
  consumers.py     intake entry point (FastStream)
  worker.py        Celery worker entry point
  outbox_relay.py  relay entry point
  core/            config.py, logging.py, errors.py, error_codes.py, db.py, inbox.py, outbox.py, clock.py, ids.py
  conversion/      the conversion feature
    domain/          formats, route, route_planner, options, ports (protocols), exceptions,
                     models.py (ConversionItem and its rules), events.py, values.py, policies.py
    application/     commands, results, services/ (convert_file, accept_conversion, process_conversion)
    domain/document_model.py  pages -> text blocks and tables: the hub for data conversions
    infrastructure/  converters/ (one adapter per library), registry.py, model_file.py (the model as JSON),
                     persistence/ (SQLAlchemy), messaging/ (RabbitMQ), storage.py (MinIO), tasks.py (Celery)
migrations/        Alembic; 0001_baseline creates inbox_message, outbox_event, conversion_item
tests/unit/        mirrors src/
tests/integration/ real PostgreSQL, RabbitMQ and MinIO in containers
```

Convert files locally, or ask the planner how a conversion would run:

```bash
uv run python -c "
from conversion.conversion.domain.formats import Format
from conversion.conversion.domain.route_planner import RoutePlanner, RouteRequest
print(RoutePlanner().get_route(RouteRequest(source=Format.CSV, target=Format.PDF)).converters)"
```

```bash
uv run python -c "
from pathlib import Path
from conversion.bootstrap import build_convert_file_service
from conversion.conversion.application.commands import ConvertFileCommand
from conversion.conversion.domain.formats import Format
from conversion.core.config import get_settings
result = build_convert_file_service(get_settings()).convert(ConvertFileCommand(
    source=Format.JPEG, target=Format.PDF, inputs=(Path('photo.jpg'),), output_dir=Path('out')))
print(result.outputs, result.page_count)"
```
