# 1. On-device model runtime for mobile extraction

Status: Proposed · Date: 2026-10-22 · Deciders: mobile extraction owner (to approve)

## Context

IDP-04 Ch. 3.1 leaves the on-device small language model open (decision D-2, IDP-00 Ch. 10): candidates are MediaPipe LLM Inference or llama.cpp, chosen per RAM tier, delivered with Play Asset Delivery. D-2 is owned by the mobile lead and is needed by Phase 4.

`mobile/idp-android` already runs vision-language models through llama.cpp:

- `llama-mtmd-cli` (one process per extraction) and `llama-server` (started once, reused over loopback HTTP), both shipped as renamed `.so` files and executed from `nativeLibraryDir` (`useLegacyPackaging = true`).
- The shipped build is CPU-only: ARMv8.2 with dot-product and fp16, OpenMP, Vulkan off. A Vulkan GPU build was tried and dropped because it gave poor results.
- Models tried so far: Qwen3-VL (2B and 4B), LFM2-VL-3B, MiniCPM-V 4.6, PaddleOCR-VL and VisionPsy-Nano, each with its own prompt, GBNF grammar and image-token settings. PaddleOCR-VL support was removed from the app on 2026-10-22 because it is not needed; the measurements stay in the findings docs. Measurements are in `docs/mobile-extraction/`.
- ML Kit text recognition for the OCR-first path.

## Decision

Keep llama.cpp as the on-device runtime for now. Do not start a MediaPipe migration until D-2 is decided.

The runtime sits behind one interface (`:engine:slm`, IDP-04 Ch. 3.2) so the choice can change without touching the UI or the extraction rules. Choosing the model per RAM tier (decision D-2) is front-end work and is outside this standardization; the model is picked by hand, as today.

## Consequences

- No migration cost now; the measured per-model profiles stay valid.
- The loopback `llama-server` process and its cleartext HTTP are a deviation from IDP-04 Ch. 3.8 (HTTPS only). See ADR 0002 for the mitigation.
- Models are not delivered through Play Asset Delivery yet; they are pushed to the app's files directory by hand. This is a gap to close before release.
- If D-2 picks MediaPipe, only `:engine:slm` changes.

## Alternatives

- MediaPipe LLM Inference: no native-process packaging and a supported delivery path, but no measured results on our models or document types. Not rejected, just not evaluated.
- Both runtimes at once: doubles the testing surface; deferred until there is evidence one is needed.

## Update 2026-10-22: native build finding

The pinned build (`native/build_llama_dotprod.sh`, llama.cpp `000bee5`) is not shown to reproduce the libraries the app shipped with. The "dist_dot" libraries built from that commit on another machine split LFM2 images into tiles (upstream PR #27057), so LFM2 `pan_full` takes about 90 s against 25 to 36 s with the old libraries. Qwen3-VL and MiniCPM-V are not affected. Options A (older commit), B (resize the LFM2 input to 500 px) and C (keep the old `libmtmd.so`) are in `docs/mobile-extraction/STANDARDIZATION_HANDOFF.md`, section 5. The decision is open and this ADR stays Proposed. Update 2026-10-23: option B is implemented in the working tree and recorded in ADR 0007.
