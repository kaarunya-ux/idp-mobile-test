# The local integration stack. `make prepare` does every slow step ahead of time; `make up` then only starts.
.PHONY: prepare env models pull build up wait check status logs down reset

prepare: env models pull build  ## once, and after pulling new code: everything ready, nothing left to build
	@echo "Ready. Start it with: make up"

env:
	@test -f .env || (cp .env.example .env && echo "created .env from .env.example")

models:  ## the OCR models (65 MB), checked against extraction-service/models.yaml
	python3 fetch_models.py

pull:  ## the infrastructure images (PostgreSQL, Redis, RabbitMQ, MinIO, Keycloak)
	docker compose --profile infra pull --ignore-buildable

build:  ## our images (extraction, ai-gateway, conversion); the first build takes several minutes
	docker compose --profile infra --profile check build

up:  ## start without building, then wait until every service is healthy
	docker compose up -d --no-build
	@$(MAKE) --no-print-directory wait status

wait:  ## up to 5 minutes for every health-checked service to be healthy (the one-time jobs just finish)
	@for i in $$(seq 1 60); do \
		pending=$$(docker compose ps --format '{{.Service}} {{.Health}}' | awk '$$2 == "starting" || $$2 == "unhealthy" {print $$1}'); \
		failed=$$(docker compose ps -a --format '{{.Service}} {{.ExitCode}} {{.State}}' | awk '$$3 == "exited" && $$2 != "0" {print $$1}'); \
		if [ -n "$$failed" ]; then echo "stopped with an error: $$failed (make logs s=<name>)"; exit 1; fi; \
		if [ -z "$$pending" ]; then echo "all services healthy"; exit 0; fi; \
		sleep 5; \
	done; echo "still not healthy after 5 minutes: $$pending"; exit 1

check:  ## the readiness check: health, tokens and the API, one extraction and one conversion end to end
	docker compose run --rm --no-deps stack-check

status:
	docker compose ps --format "table {{.Service}}\t{{.Status}}\t{{.Ports}}"

logs:  ## every service, newest lines; one service: make logs s=extraction-worker
	docker compose logs -f --tail 100 $(s)

down:  ## stop, keep the data
	docker compose down

reset:  ## stop and delete the data (databases, buckets): the next `make up` starts clean
	docker compose --profile infra down -v
