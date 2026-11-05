# The local integration stack. `make prepare` does every slow step ahead of time; `make up` then only starts.
.PHONY: prepare env models pull build up wait check status logs down reset use-mac-model use-mock-model

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

# The Mac mini's model (Qwen3.5-4B on mlx_vlm.server), for when the backend tests with real answers. Only ai-gateway
# restarts; `make up` or `make use-mock-model` puts the mock back. mlx_vlm.server breaks enforced schemas: none is sent.
MAC_MODEL_URL ?= http://10.0.3.99:8082/v1
MAC_MODEL_NAME ?= models/Qwen3.5-4B-MLX-4bit
# Conversion's page reading: the Qwen3.6-35B on the other Mac mini (llama-server). PAGE_MODEL_URL= (empty) sends it
# to the 4B too. Its address has changed before: check it with curl <address>/models
PAGE_MODEL_URL ?= http://10.0.3.154:8080/v1
PAGE_MODEL_NAME ?= qwen3.6-35b-a3b

use-mac-model:  ## AI answers from the Mac mini's real model (until the next `make up`)
	MODEL_BASE_URL=$(MAC_MODEL_URL) MODEL_NAME=$(MAC_MODEL_NAME) MODEL_SEND_SCHEMA=false \
		PAGE_MODEL_URL=$(PAGE_MODEL_URL) PAGE_MODEL_NAME=$(PAGE_MODEL_NAME) \
		docker compose up -d --no-build --no-deps --force-recreate ai-gateway
	@echo "ai-gateway now uses $(MAC_MODEL_NAME) at $(MAC_MODEL_URL)"
	@echo "and, for conversion's page reading, $(if $(PAGE_MODEL_URL),$(PAGE_MODEL_NAME) at $(PAGE_MODEL_URL),the same model)"

use-mock-model:  ## back to the mock model's made-up answers
	docker compose up -d --no-build --no-deps --force-recreate ai-gateway
	@echo "ai-gateway now uses the mock model"

down:  ## stop, keep the data
	docker compose down

reset:  ## stop and delete the data (databases, buckets): the next `make up` starts clean
	docker compose --profile infra down -v
