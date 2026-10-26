# 1. Read models.yaml and inference.yml without a YAML library

Status: Proposed (awaiting the Python lead)

## Context

Python Engineering Standard ML-01 says every model is declared in a registry file called `models.yaml`. Reading YAML
normally needs a parser, and none is in the approved library list (standard ch. 22.3), so DEP-02 would require an ADR
for one. PaddleOCR models also ship an `inference.yml` with the character set and detector thresholds the engine needs.

## Decision

Keep the file name ML-01 asks for and add no dependency. `idp_ocr_engines/simple_yaml.py` reads the few YAML forms
these files use (comment lines, `key: value`, `- item` lists, plain, single- and double-quoted values).
`registry.parse_registry` accepts only the registry's fixed layout, and `paddle_config.read_post_process` only the
`PostProcess` section. Anything else raises an error instead of being guessed at. Both readers were checked against
PyYAML: the 9 registry entries, the three character sets (568, 18,383 and 18,708 entries) and the thresholds are identical.

## Consequences

Good: ML-01 is followed to the letter and nothing outside the approved list is added; `uvicorn[standard]` (which pulled
in PyYAML and python-dotenv, which the standard forbids in application code) became plain `uvicorn`.
Bad: a small reader is code we maintain. A registry edit that uses other YAML features (anchors, flow style, multi-line
values) is rejected with a clear error until the reader is extended. The optional `indra` extra still installs PyYAML
through `huggingface-hub`; our code never imports it.

## Alternatives considered

PyYAML or ruamel.yaml (not on the list). A registry in TOML or JSON (breaks ML-01's file name).
