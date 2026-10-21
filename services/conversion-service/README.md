# conversion-service

Converts documents between PDF, Word, Excel, CSV, PowerPoint and images. Also applies PDF edit plans, merges PDFs, exports extraction results and renders page previews. Specified in IDP-03 ch. 3 and built to the Python Engineering Standard.

There is **no public API**. document-service (Java) sends `ConversionRequested` messages over RabbitMQ, and this service replies with `JobProgressed`, `ConversionCompleted` or `WorkFailed` (IDP-00 ch. 7.6). Files go in and out through MinIO; messages carry only their locations.

**Status:** step 2 of the [build log](../../docs/conversion-service/build-log.md). There's a health server, settings and logging, plus the route planner, which decides how to get from any format to any other. Nothing converts files yet; that logic is being ported from `services/conversions/`.

## Run it

```bash
uv sync          # create .venv from uv.lock, with dev tools
make check       # format, lint, types, architecture rules, tests: exactly what CI runs
make run         # health server on http://127.0.0.1:8001/health/live
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

For local runs, copy `.env.example` to `.env`.

## Layout

```
src/conversion/
  health.py      probe endpoints (/health/live, /health/ready)
  core/          config.py (settings), logging.py (structured logs), errors.py + error_codes.py
  conversion/    the conversion feature
    domain/      formats.py, route.py (Route, Step, Converter, Fidelity), route_planner.py, exceptions.py
tests/unit/      mirrors src/
```

Ask the planner how a conversion would run:

```bash
uv run python -c "
from conversion.conversion.domain.formats import Format
from conversion.conversion.domain.route_planner import RoutePlanner, RouteRequest
print(RoutePlanner().get_route(RouteRequest(source=Format.CSV, target=Format.PDF)).converters)"
```
