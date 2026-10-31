# Image layout from the Python Engineering Standard, ch. 23: build with uv, run as a non-root user (DPL-01), tag with the git
# SHA when building (DPL-02), separate targets for the API and the worker (DPL-05, ML-09).
#
#   docker build --target api    -t idox-conversion-api:$(git rev-parse --short HEAD) .
#   docker build --target worker -t idox-conversion-worker:$(git rev-parse --short HEAD) .
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS build-api
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev

FROM build-api AS build-worker
# The worker libraries (the model client, Pillow) are in the `workers` extra, so the API image never has them (ML-09).
RUN uv sync --frozen --no-dev --extra workers

# The API image: only the health probes (DPL-03). No CV, OCR or ML libraries (ML-09).
FROM python:3.12-slim-bookworm AS api
RUN useradd --system --uid 10001 app
WORKDIR /app
COPY --from=build-api /app /app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER 10001
EXPOSE 8001
CMD ["uvicorn", "idox_conversion.main:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "2", "--proxy-headers", "--timeout-graceful-shutdown", "30"]

# The worker image: the model client and the prompts it loads (LLM-02). The command that starts a worker is added with the
# worker step (standard ch. 16); until then this target builds the libraries and nothing runs from it.
FROM python:3.12-slim-bookworm AS worker
RUN useradd --system --uid 10001 app
WORKDIR /app
COPY --from=build-worker /app /app
COPY prompts ./prompts
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER 10001
