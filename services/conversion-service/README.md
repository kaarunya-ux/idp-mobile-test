# conversion-service

Converts documents between PDF, Word, Excel, CSV, PowerPoint and images. Also applies PDF edit plans, merges PDFs, exports extraction results and renders page previews. Specified in IDP-03 ch. 3 and built to the Python Engineering Standard.

There is **no public API**. document-service (Java) sends `ConversionRequested` messages over RabbitMQ, and this service replies with `JobProgressed`, `ConversionCompleted` or `WorkFailed` (IDP-00 ch. 7.6). Files go in and out through MinIO; messages carry only their locations.

**Status:** step 3c of the [build log](../../docs/conversion-service/build-log.md). On local files it runs every conversion in the IDP-00 matrix that needs no OCR (36 of 47 routes): PNG ↔ JPEG, images ↔ PDF, Office → PDF and images, CSV ↔ XLSX, images and PDF → PowerPoint, and the data routes through the document model (Word, Excel, CSV, PowerPoint and digital PDF in; Word, Excel, CSV and PowerPoint out). Scanned PDF and image → Word/Excel/CSV wait for OCR; RabbitMQ and MinIO come in step 4.

## System dependencies

The workers need these **system packages**. They aren't Python packages, so they aren't in `pyproject.toml`; the worker Docker image will install them in step 4.

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
make check             # format, lint, types, architecture rules, tests: exactly what CI runs
make run               # health server on http://127.0.0.1:8001/health/live
```

## Processes

| Process | Command | Status |
|---|---|---|
| health HTTP (probes only) | `uvicorn conversion.health:app --port 8001` | step 1 |
| `intake` (FastStream consumer) | `faststream run conversion.consumers:app` | step 4 |
| `worker-office` / `-pdf` / `-data` / `-ocr` (Celery) | `celery -A conversion.worker worker -Q <queue>` | steps 3–4 |
| `relay` (outbox publisher) | `python -m conversion.outbox_relay` | step 4 |

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

For local runs, copy `.env.example` to `.env`.

## Layout

```
src/conversion/
  health.py        probe endpoints (/health/live, /health/ready)
  bootstrap.py     composition root: builds the services and converters once per process
  core/            config.py, logging.py, errors.py, error_codes.py
  conversion/      the conversion feature
    domain/          formats, route, route_planner, options, ports (protocols), exceptions
    application/     commands, results, services/convert_file.py (ConvertFileService)
    domain/document_model.py  pages -> text blocks and tables: the hub for data conversions
    infrastructure/  converters/ (one adapter per library), registry.py, model_file.py (the model as JSON)
tests/unit/        mirrors src/
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
