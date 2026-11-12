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

.PHONY: help all injection injection-conversion injection-extraction pii drift drift-baseline full-check clean accuracy-aadhaar accuracy-pan test-accuracy

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

full-check:  ## the suites, then the drift comparison, in one go (run by hand, ENV=preprod)
	-$(MAKE) injection
	-$(MAKE) pii
	$(GATEWAY) drift/compare.py

clean:  ## remove drawn pictures and results
	rm -rf prompt-injection/*/pages prompt-injection/*/cards prompt-injection/*/results pii-compliance/pages pii-compliance/results

accuracy-aadhaar:  ## 100 invented Aadhaar cards sent to POST /api/v1/extract, scored per field; needs EVAL_EXTRACTION_URL (about 12 minutes)
	$(EXTRACTION) accuracy/aadhaar/run.py
	$(EXTRACTION) accuracy/aadhaar/report.py

accuracy-pan:  ## 100 invented PAN cards sent to POST /api/v1/extract, scored per field; needs EVAL_EXTRACTION_URL (about 10 minutes)
	$(EXTRACTION) accuracy/pan/run.py
	$(EXTRACTION) accuracy/pan/report.py

test-accuracy:  ## the accuracy sets' own tests (no service needed)
	$(EXTRACTION) -m pytest -q tests/test_accuracy_aadhaar.py tests/test_accuracy_pan.py
