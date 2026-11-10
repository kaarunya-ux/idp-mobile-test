# 7. A model registry for the pre-prod model

Status: Proposed · Date: 2026-11-10 · Deciders: Python team lead (Prakyath)

## Context

The standard asks for every model to be declared in `models.yaml` (ML-01) and for model files to be verified by checksum,
never fetched at request time (ML-02). The gateway only had a model *name* and a URL per server, so a model swapped on the
host changed behaviour with nothing in the code or the logs to say so (prompt drift strategy 4). Dev runs two models (a
Qwen3.5-4B for cards, a Qwen3.6-35B for pages, ADR 0005). For pre-prod the team chose one: **Qwen3.6-35B-A3B,
UD-IQ4_XS** (`Qwen3.6-35B-A3B-UD-IQ4_XS.gguf`), for every role.

## Decision

1. **`models.yaml` in the service root**, shipped in the image. One entry: name, version, quantisation, source and
   revision (`unsloth/Qwen3.6-35B-A3B-GGUF` at `a483e9e6cbd5`), runtime `llama.cpp`, device `gpu`, the id the server answers
   with, the roles it serves (`vl`, `smart`, `auto`, `page`) and the file with its **published** SHA-256
   (`649d7508507b84638732c4f52c24c8b15843c6dca2f3ff793ae07c14a67ebbb3`) and size (17,730,509,792 bytes).
2. **No YAML library.** None is on the approved list (ch. 22.3), so the registry uses a narrow YAML subset read by a
   small reader in `model_registry.py`, as in extraction-service ADR 0001. Anything else is refused with an error.
3. **The checksum is verified where the file is placed**, with `scripts/verify_model.py`, because the gateway does not hold
   the file (a llama-server on another host does). The gateway verifies what it can: at start-up it asks each role's
   server which model it serves and compares it with the registry (`MODEL__REGISTRY_CHECK`: `off`, `warn`, `strict`).
   The default is `off`, so dev, which serves other models, is unchanged; pre-prod and prod set `strict`.
4. **A server that cannot be asked** is logged, not fatal: the circuit breaker already handles a model that is down, and a
   gateway that refuses to start whenever the model server restarts would be worse.
5. **The registry's hash is part of `config_hash`**, so a change of model changes the hash on every log line, in
   `ai_build_info`, and in the golden-test stamp (drift strategies 3, 4 and 24).

## What the real server showed

The first version matched the id the server lists at `/v1/models`. Against the pre-prod Mac it failed in `strict`: the
server had been restarted with `--alias qwen3.6-35b`, so `/v1/models` listed only that alias, not the model file. The
model is now recognised from llama-server's `/props` (`model_path`, the file it loaded); when `/props` reports a file,
the file name decides and the alias is ignored, so a model swapped behind the same alias is caught. `served_as` may list
several aliases for servers without `/props`. A start-up wait (`MODEL__REGISTRY_WAIT`) covers a model server that is still
loading when the gateway starts, which is the normal order on a VM.

## Consequences

- Pre-prod can no longer drift silently to another model: a different model fails the start-up with `strict`, and a
  different registry changes the configuration hash.
- The checksum is the one Hugging Face publishes for that file at that revision. Whether the file on the model host is
  identical is established only by running `scripts/verify_model.py` there. It has not been run on the Mac mini.
- The vision encoder (`mmproj-*.gguf`; the repository publishes BF16, F16 and F32) is not pinned: which one the host loads
  was not confirmed. A swapped encoder would not be noticed until it is added to the registry.
- The check compares names, not weights: a different file with the same name passes the start-up check and is caught only
  by the checksum script.
- The 4B card model dev uses is not in the registry; with `warn` in dev it is logged as a mismatch for the `vl` role.
- The golden tests were measured on this model (the 35B) for both pages and cards, so they describe pre-prod as chosen.
  Dev's card reads on the 4B were not measured.

## Alternatives considered

- PyYAML or ruamel.yaml for the registry: not on the approved list, so it would need its own ADR (DEP-02).
- Hashing the model file from the gateway: it does not have the file, and 17 GB cannot be fetched over HTTP at start-up.
- Making `strict` the default: it would stop every dev gateway that serves the 4B for cards.
