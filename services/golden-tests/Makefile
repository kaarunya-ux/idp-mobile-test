# Golden tests against a live model server (ML-12). Each suite runs in the environment of the service whose code it
# exercises (`uv run --project`). The model server needs vision on (`--mmproj`); set GOLDEN_MODEL_URL if it is not the
# default in model.py. Every target exits 1 when a suite fails. Run `make help` for the list.
SERVICES := ..
ENV ?= dev
export GOLDEN_ENV = $(ENV)
# Override these on a machine without uv, for example GATEWAY=/opt/idp/ai-gateway/.venv/bin/python
GATEWAY ?= uv run --project $(SERVICES)/ai-gateway python
EXTRACTION ?= uv run --project $(SERVICES)/extraction-service python
CONVERSION ?= uv run --project $(SERVICES)/conversion-service python

.PHONY: help all injection injection-conversion injection-extraction pii drift drift-baseline gate canary full-check logs-record logs-check prompt-ab prompt-verdict prompt-quality test-tools clean

help:  ## what can run
	@grep -E '^[a-z-]+:.*##' Makefile | sed 's/:.*##/\t/'

all: injection pii  ## everything

injection: injection-conversion injection-extraction  ## prompt injection: conversion pages and extraction cards

injection-conversion:  ## 12 pages (digital, scan-style, hostile) read with page-read; about 5 minutes
	$(GATEWAY) prompt-injection/conversion/build.py
	$(GATEWAY) prompt-injection/conversion/run.py

injection-extraction:  ## 21 cards, plain and hostile, read by ReadFieldsService; about 8 minutes
	$(GATEWAY) prompt-injection/extraction/build.py
	$(GATEWAY) prompt-injection/extraction/run.py

pii:  ## PII compliance: no personal data in gateway or conversion logs, errors, metrics; extraction masks
	$(GATEWAY) pii-compliance/build.py
	$(GATEWAY) pii-compliance/run_gateway.py
	$(EXTRACTION) pii-compliance/run_extraction.py
	$(CONVERSION) pii-compliance/run_conversion.py

drift:  ## compare the latest results with this environment's baseline (ENV=dev|preprod|prod)
	$(GATEWAY) drift/compare.py

drift-baseline:  ## record the latest results as this environment's baseline (ARGS='--accept-failures "reason"')
	$(GATEWAY) drift/baseline.py $(ARGS)

gate:  ## promotion gate: ENV=preprod FROM=dev (ARGS='--accept-change "reason"')
	$(GATEWAY) drift/gate.py --from $(FROM) $(ARGS)

canary:  ## one small page read and checked against its known content
	$(GATEWAY) drift/canary.py

full-check:  ## the suites, then the drift comparison, in one go (run by hand, ENV=preprod)
	-$(MAKE) injection
	-$(MAKE) pii
	$(GATEWAY) drift/compare.py

logs-record:  ## learn normal from the logs of a good period: LOGS='/var/log/idp/*.jsonl'  (ENV=preprod)
	$(GATEWAY) drift/logwatch.py record --env $(ENV) $(LOGS)

logs-check:  ## compare recent logs with that: LOGS='/var/log/idp/*.jsonl' SINCE=1h; alerts are JSON lines, exit 1 on an alert
	$(GATEWAY) drift/logwatch.py check --env $(ENV) --since $(or $(SINCE),1h) $(LOGS)

prompt-ab:  ## did a new prompt version make things worse? PROMPT=page-read OLD=2 NEW=3 (about 12 minutes); prints the rollback
	$(GATEWAY) drift/prompt_check.py ab --prompt $(PROMPT) --old $(OLD) --new $(NEW)

prompt-verdict:  ## the same verdict from results already kept under the tags old and new (no model needed)
	$(GATEWAY) drift/prompt_check.py ab --reuse --prompt $(PROMPT) --old $(OLD) --new $(NEW)

prompt-quality:  ## judge every prompt on a second, different document set (about 20 minutes): ARGS='--only classify' or ARGS='--candidate page-read=/path/file.md'
	$(GATEWAY) prompt-quality/suite.py $(ARGS)

test-tools:  ## unit tests of the drift tools (no model needed)
	$(GATEWAY) -m pytest tests -q

clean:  ## remove drawn pictures and results
	rm -rf prompt-injection/*/pages prompt-injection/*/cards prompt-injection/*/results pii-compliance/pages pii-compliance/results
