# Targets from the Python Engineering Standard, ch. 22.2. `make check` runs exactly what CI runs.
.PHONY: sync fmt check test run

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
	uv run pytest -m "not slow" --cov=conversion --cov-report=term-missing --cov-fail-under=80

test:
	uv run pytest

run:
	uv run uvicorn conversion.health:app --host 127.0.0.1 --port 8001 --reload
