# Image layout from the Python Engineering Standard, ch. 23: build with uv, run as a non-root user. Three targets,
# so each process ships only what it needs (DPL-05):
#
#   docker build --target api     -t extraction-service:api .      # the HTTP API: no OpenCV, OCR or models (ML-09)
#   docker build --target service -t extraction-service:service .  # intake consumer, outbox relay, migrations
#   docker build --target worker  -t extraction-service:worker .   # the pipeline: OpenCV, ONNX Runtime, OCR engines
#                                                                   # (-Q pipeline; a second pool with -Q ai for deep analysis)
#
# The OCR models are not in the image: mount them at /models (OCR__MODELS_DIR). models.yaml, which pins their
# checksums, is in the image, so a wrong or changed model file is refused at start-up (ML-01).

# --- Build stages -------------------------------------------------------------
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS build-api
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS build-workers
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --extra workers --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --extra workers

# --- Runtime stages -----------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime
RUN useradd --system --uid 10001 --create-home app
WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

FROM runtime AS api
COPY --from=build-api /app /app
USER 10001
EXPOSE 8000
CMD ["uvicorn", "extraction.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--proxy-headers", "--timeout-graceful-shutdown", "30"]

FROM runtime AS service
COPY --from=build-workers /app /app
COPY alembic.ini ./
COPY migrations ./migrations
USER 10001
# Overridden per process: `python -m extraction.outbox_relay`, or `alembic upgrade head` as a pre-deploy job.
CMD ["faststream", "run", "extraction.consumers:create_app", "--factory"]

FROM runtime AS worker
# OpenCV (headless) needs GLib at run time. LibreOffice turns Office documents into PDFs (ADR 0008, as in
# conversion-service); Noto fonts so Devanagari and other scripts never render as boxes.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libglib2.0-0 libreoffice-writer libreoffice-calc libreoffice-impress fonts-noto-core fonts-noto-extra \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build-workers /app /app
COPY models.yaml ./
ENV OCR__MODELS_DIR=/models HOME=/home/app
USER 10001
CMD ["celery", "-A", "extraction.worker", "worker", "-Q", "pipeline", "-c", "4", "--prefetch-multiplier", "1"]
