# Targets from the Python Engineering Standard, ch. 22.2. `make check` runs exactly what CI runs.
.PHONY: sync fmt check test-all audit run mock-model

sync:
	uv sync

fmt:
	uv run ruff format .
	uv run ruff check --fix .

check:
	uv run ruff format --check .
	uv run ruff check .
	uv run mypy src
	uv run lint-imports
	uv run pytest -m "not slow" --cov=ai_gateway --cov=mock_model --cov-report=term-missing --cov-fail-under=80

test-all:
	uv run pytest

audit:
	uv run pip-audit
	gitleaks detect --no-banner --redact

run:
	uv run uvicorn ai_gateway.main:app --host 127.0.0.1 --port 8002 --reload

mock-model:  # the stand-in model server on :4000; run the gateway with MODEL__BASE_URL=http://localhost:4000/v1
	uv run uvicorn mock_model.main:app --host 127.0.0.1 --port 4000
