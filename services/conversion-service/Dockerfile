# Image layout from the Python Engineering Standard, ch. 23: build with uv, run as a non-root user. Three targets,
# so each process ships only what it needs (DPL-05):
#
#   docker build --target health  -t conversion-service:health .   # probes only, no converter libraries
#   docker build --target service -t conversion-service:service .  # intake consumer, outbox relay, migrations
#   docker build --target worker  -t conversion-service:worker .   # Celery workers, with LibreOffice and fonts

# --- Build stages -------------------------------------------------------------
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS build-health
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

FROM runtime AS health
COPY --from=build-health /app /app
USER 10001
EXPOSE 8001
CMD ["uvicorn", "conversion.health:app", "--host", "0.0.0.0", "--port", "8001", "--timeout-graceful-shutdown", "30"]

FROM runtime AS service
COPY --from=build-workers /app /app
COPY alembic.ini ./
COPY migrations ./migrations
USER 10001
# Overridden per process: `python -m conversion.outbox_relay`, or `alembic upgrade head` as a pre-deploy job.
CMD ["faststream", "run", "conversion.consumers:create_app", "--factory"]

FROM runtime AS worker
# LibreOffice for Office -> PDF; Noto fonts so Devanagari and other scripts never render as boxes (IDP-03 ch. 3.4).
# libglib2.0-0 for OpenCV, which the OCR engine uses (AI_GATEWAY__PAGE_READER=ocr_layout, ADR 0001).
# Tesseract (English and Hindi) for the second reading that checks the model's scanned pages (ADR 0003).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libglib2.0-0 libreoffice-writer libreoffice-calc libreoffice-impress fonts-noto-core fonts-noto-extra \
        tesseract-ocr tesseract-ocr-eng tesseract-ocr-hin \
    && rm -rf /var/lib/apt/lists/*
# tini as PID 1: LibreOffice leaves helper processes behind (about 6 per document), which pass to PID 1 when their parent
# ends. Neither uvicorn nor Celery reaps them, so they would pile up as zombies.
RUN apt-get update && apt-get install -y --no-install-recommends tini && rm -rf /var/lib/apt/lists/*
ENTRYPOINT ["/usr/bin/tini", "--"]
COPY --from=build-workers /app /app
# The OCR models are not in the image: they are mounted at OCR__MODELS_DIR. models.yaml pins their checksums (ML-01).
COPY models.yaml ./
# LibreOffice writes its profile under HOME; the app user's home is writable, the image isn't.
ENV HOME=/home/app
USER 10001
CMD ["celery", "-A", "conversion.worker", "worker", "-Q", "office,pdf,data", "-c", "2", "--prefetch-multiplier", "1"]
