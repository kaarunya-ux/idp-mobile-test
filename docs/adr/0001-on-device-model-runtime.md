# 1. On-device model runtime for mobile extraction

Status: Proposed · Date: 2026-10-22 · Deciders: mobile extraction owner (to approve)

## Context

IDP-04 Ch. 3.1 leaves the on-device small language model open (decision D-2, IDP-00 Ch. 10): candidates are MediaPipe LLM Inference or llama.cpp, chosen per RAM tier, delivered with Play Asset Delivery. D-2 is owned by the mobile lead and is needed by Phase 4.

`mobile/idp-android` already runs vision-language models through llama.cpp:

- `llama-mtmd-cli` (one process per extraction) and `llama-server` (started once, reused over loopback HTTP), both shipped as renamed `.so` files and executed from `nativeLibraryDir` (`useLegacyPackaging = true`).
- Models tried so far: Qwen3-VL (2B and 4B), LFM2-VL-3B, MiniCPM-V 4.6, PaddleOCR-VL and VisionPsy-Nano, each with its own prompt, GBNF grammar and image-token settings. Measurements are in `docs/mobile-extraction/`.
- ML Kit text recognition for the OCR-first path.

## Decision

Keep llama.cpp as the on-device runtime for now. Do not start a MediaPipe migration until D-2 is decided.

The runtime sits behind one interface (`:engine:slm`, IDP-04 Ch. 3.2) so the choice can change without touching the UI or the extraction rules. Model choice per RAM tier remains open and needs the evaluation set (Python Standard ML-11).

## Consequences

- No migration cost now; the measured per-model profiles stay valid.
- The loopback `llama-server` process and its cleartext HTTP are a deviation from IDP-04 Ch. 3.8 (HTTPS only). See ADR 0002 for the mitigation.
- Models are not delivered through Play Asset Delivery yet; they are pushed to the app's files directory by hand. This is a gap to close before release.
- If D-2 picks MediaPipe, only `:engine:slm` changes.

## Alternatives

- MediaPipe LLM Inference: no native-process packaging and a supported delivery path, but no measured results on our models or document types. Not rejected, just not evaluated.
- Both runtimes at once: doubles the testing surface; deferred until there is evidence one is needed.
