# Targets from the Python Engineering Standard, ch. 22.2. `make check` runs exactly what CI runs.
.PHONY: sync fmt check test-all audit run migrate

sync:
	uv sync --all-extras

fmt:
	uv run ruff format .
	uv run ruff check --fix .

check:
	uv run ruff format --check .
	uv run ruff check .
	uv run mypy src
	uv run lint-imports
	uv run pytest -m "not slow" --cov=extraction --cov=idp_ocr_engines --cov-report=term-missing --cov-fail-under=80

test-all:
	uv run pytest

audit:
	uv run pip-audit
	gitleaks detect --no-banner --redact

run:
	uv run uvicorn extraction.main:app --host 127.0.0.1 --port 8000 --reload

migrate:
	uv run alembic upgrade head
