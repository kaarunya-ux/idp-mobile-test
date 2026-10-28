# Image layout from the Python Engineering Standard, ch. 23: build with uv, run as a non-root user. One process, the
# HTTP API on port 8002 (IDP-03 ch. 4.1). The prompts are package files, so they ship inside the image.
#
#   docker build --target api -t ai-gateway .
#   docker build --target mock-model -t mock-model .   # laptops and CI only: answers on :4000 (IDP-03 ch. 5)

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS build
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev

FROM python:3.12-slim-bookworm AS api
RUN useradd --system --uid 10001 --create-home app
WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY --from=build /app /app
USER 10001
EXPOSE 8002
CMD ["uvicorn", "ai_gateway.main:app", "--host", "0.0.0.0", "--port", "8002", "--workers", "2", "--proxy-headers", "--timeout-graceful-shutdown", "30"]

FROM api AS mock-model
EXPOSE 4000
CMD ["uvicorn", "mock_model.main:app", "--host", "0.0.0.0", "--port", "4000"]
