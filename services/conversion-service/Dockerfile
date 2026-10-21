# Image layout from the Python Engineering Standard, ch. 23: build with uv, run as a non-root user.
# Step 1 runs only the health server; worker images (LibreOffice, OCR) get their own targets later (DPL-05).
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS build
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev

FROM python:3.12-slim-bookworm AS runtime
RUN useradd --system --uid 10001 app
WORKDIR /app
COPY --from=build /app /app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER 10001
EXPOSE 8001
CMD ["uvicorn", "conversion.health:app", "--host", "0.0.0.0", "--port", "8001", "--timeout-graceful-shutdown", "30"]
