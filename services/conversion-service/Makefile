# Targets from the Python Engineering Standard, ch. 22.2. `make check` runs exactly what CI runs.
# Integration tests start PostgreSQL, RabbitMQ and MinIO in Docker; `make check-unit` runs without Docker.
.PHONY: sync fmt lint check check-unit test run migrate up down

sync:
	uv sync --all-extras

fmt:
	uv run ruff format .
	uv run ruff check --fix .

lint:
	uv run ruff format --check .
	uv run ruff check .
	uv run mypy src
	uv run lint-imports

# coverage.xml is what SonarQube reads (sonar-project.properties).
check: lint
	uv run pytest -m "not slow" --cov=conversion --cov-report=term-missing --cov-report=xml --cov-fail-under=80

check-unit: lint
	uv run pytest -m "not slow and not integration" --cov=conversion --cov-report=term-missing

test:
	uv run pytest

run:
	uv run uvicorn conversion.health:app --host 127.0.0.1 --port 8001 --reload

migrate:
	uv run alembic upgrade head

up:
	docker compose up --build -d

down:
	docker compose down -v
