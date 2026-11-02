# Qwen3-VL-8B on-device latency — 8GB-RAM phone, and the 6GB-RAM-phone optimization re-tests (2026-10-31)

Covers two separate, non-overlapping phases of testing, kept in one document because the second
phase's uncommitted code change (`-ctk q8_0 -ctv q8_0` in `LlamaLaunch.kt`) was motivated directly
by the finding in the first phase. Neither phase changed any shipped app code or behavior; the
`-ctk`/`-ctv` change is still sitting **uncommitted** on the `mobile_standards` branch, not yet
latency-measured, as flagged in its own code comment.

**Device note:** the "8GB" and "6GB" phones referred to below are two distinct physical Android
units in the same device family used for this project (confirmed distinct serials). All numbers
below are per-device and must not be assumed to carry over to the other unit or to a different
chipset.

---

## Part 1 — Qwen3-VL-8B on the 8GB-RAM phone: the swap-collapse finding

**Status: one confirmed qualitative finding, not a full latency study.** This was an early,
exploratory test, not a systematic sweep like the 6GB-phone work in Part 2 below.

**What was observed:** running the Qwen3-VL-8B model on the 8GB-RAM phone, token-generation speed
collapsed from a normal **~15 tok/s down to ~0.3 tok/s** once the model's resident memory set
exceeded the phone's available RAM. This is a **major-page-fault-into-swap** collapse, not a
gradual slowdown — the drop is roughly 50x, consistent with the CPU spending most of its time
servicing page faults instead of doing matmul work.

**Root cause (inferred, not separately isolated):** the 8B model's weights plus its KV cache
together don't comfortably fit in 8GB of usable RAM once the OS, the app process, and other
resident state are accounted for. Once resident set size crosses available RAM, the kernel starts
swapping, and on a phone (zram-backed, CPU-cost swap, not disk I/O — see
`ON_DEVICE_LATENCY_AND_ACCURACY_FINDINGS.md` for the zram mechanism on the other phone) every swap
access competes directly with inference for the same CPU cycles.

**Mitigation proposed, not yet measured:** quantize the KV cache to 8-bit (`-ctk q8_0 -ctv q8_0`)
to shrink its memory footprint by roughly half, buying headroom against the resident-set-exceeds-
RAM cliff. This is the uncommitted change currently sitting in `LlamaLaunch.kt` on the
`mobile_standards` branch — applied to the shared launch-flag list for **every** model family, not
just Qwen3-VL-8B, since the flag is a standard, model-agnostic llama-server option.

**What this finding does NOT establish:**
- No before/after latency numbers for `-ctk/-ctv q8_0` on the 8GB phone — the mitigation has never
  been run.
- No data on whether 8-bit KV quantization costs any accuracy on this model/device.
- No data on whether the shrunk KV cache is enough headroom to avoid the collapse entirely, or
  only delays it to a larger image/longer context.
- Qwen3-VL-8B is **not a candidate for the 6GB-RAM phone** — it doesn't fit regardless of KV-cache
  quantization. The 6GB-phone work in Part 2 uses only the 2B-class models already deployed there
  (Qwen3-VL-2B, LFM2-VL-3B, MiniCPM-V-4.6).

**Open work, if this is picked back up:** run the same `-ctk/-ctv q8_0` cold/warm comparison method
used in Part 2 below, on the 8GB phone, with Qwen3-VL-8B, to get a real before/after number instead
of the current unmeasured flag.

---

## Part 2 — 6GB-RAM-phone scratchpad re-tests (2026-10-29 to 2026-10-31)

**Device:** Samsung SM-M35x 5G family, 6GB-RAM unit (serial RZCXA1SXH5V, ~5.56GB usable RAM,
confirmed a distinct physical unit from the 5.5GB phone referenced in
`ON_DEVICE_LATENCY_AND_ACCURACY_FINDINGS.md` and the LFM2/MiniCPM findings docs).

**Scope and method.** All testing below was done entirely in a local scratchpad directory, never
touching the app's shipped code, prompts, or grammars — the real prompts
(`assets/prompts/user_*.md`) and grammars (`assets/grammars/*.gbnf`) were copied in and used
verbatim or, where noted, with a self-built crop added on top; the app itself was never modified.
"Cold" means the `llama-server` process was killed and restarted (full model reload) before every
individual measured request, unless explicitly marked otherwise. Models tested: Qwen3-VL-2B
Q4_K_M, LFM2-VL-3B Q4_K_M, MiniCPM-V-4.6. Document types: PAN (3 ground-truthed cards), Aadhaar (1
card), Driving Licence (1 card).

### 2.1 Newline stop sequence — no benefit

Tested adding a `\n` stop sequence to the PAN request. First comparison (38s vs 7s) was a
measurement artifact — prompt-cache reuse on a warm server, not the stop sequence. Re-run cold for
both variants: **38s vs 35s** — no meaningful difference. The GBNF grammar already prevents
rambling, so a stop sequence adds nothing on top of it. **Not adopted.**

### 2.2 Assistant-turn prefill ("prefill the JSON start") — works, but breaks the grammar

Continuing a partial assistant turn (e.g. pre-seeding `{"document_type":`) moves those tokens into
parallel prefill instead of one-by-one decode. Without the GBNF grammar: cold **38s baseline vs
37s prefilled** — about 1s saved. **With the grammar applied, the technique produces broken/invalid
JSON** — the grammar engine starts matching from scratch with no awareness of the already-emitted
prefix, causing duplicated or malformed nested output. **Verdict: real trade-off, not worth
it** — the app's grammar-constrained correctness guarantee is worth far more than a ~1s gain.
**Not adopted.**

### 2.3 Where the ~30s "reading" time actually goes (vision encoder vs LLM prefill)

Used `libllama-mtmd-cli.so` (not the server, which doesn't expose this split) to time the vision
encoder separately from LLM prefill, on Qwen, PAN card:

| Configuration | Vision-encoder time | Total "reading" time |
|---|---|---|
| No image-token cap (initial test — a config mistake, see below) | 20.16s | ~30.7s |
| Project-standard 200-token cap applied | 5.29s | much lower overall |

**Important correction:** the first "30s reading is slow" result was caused by my own test setup
omitting `LLAMA_ARG_IMAGE_MAX_TOKENS` entirely (473–1159 image tokens depending on the card,
instead of the project-standard 200) — not a real model or hardware finding. Once the standard cap
was applied, the picture changed completely (see 2.4).

Also checked while isolating this: thread/core pinning already correctly targets the 4 fast
performance cores (`taskset f0`, cores 4-7) and flash attention makes no measurable difference on
the vision encoder specifically (5.29s vs 5.30s on/off). Batch/ubatch size: no material effect
found at the sizes tried.

### 2.4 Image-token cap — the single biggest lever, confirmed again on this phone

Baseline (uncapped) vs capped at the project-standard 200 tokens, cold, one card each:

| Model | Document | Baseline (uncapped) | Capped (200) | Drop |
|---|---|---|---|---|
| Qwen3-VL-2B | PAN | 38s | 21s | −17s |
| Qwen3-VL-2B | Aadhaar | 74s | 24s | −50s |
| LFM2-VL-3B | Aadhaar | 146s | 40s | −106s |

Smaller/capped images were **simultaneously faster and more accurate** than full resolution in
every case tested — not a speed/accuracy trade-off. This matches the same conclusion reached
earlier for LFM2-VL-3B's PAN-only work (`LFM2_VL_3B_LATENCY_FINDINGS.md` §7) and confirms it
extends to Aadhaar on a different, lower-RAM phone.

### 2.5 MiniCPM-V-4.6: from non-functional to working, via canvas crop + disabling "thinking"

MiniCPM is not driven by the image-token cap the way Qwen/LFM2 are — its real latency lever in the
shipped app is the **field-canvas-crop technique** (`buildFieldCanvasFile` in
`ImagePreparation.kt`, PAN-specific: crops field regions into one small 448×448 canvas, avoiding
MiniCPM's tile-based image slicer, which splits anything over 448px/side into 3+ tiles at ~7s
each).

**Before any fix:** MiniCPM never produced an answer on Aadhaar, at any configuration tried (400 or
1200 max_tokens, 560px-resize or full-resolution) — it burned its entire token budget on internal
`reasoning_content` and returned `finish_reason="length"` with empty content. Every attempt took
83–109s and produced nothing.

**Fix 1 — crop canvas for Aadhaar/DL (new, self-built in scratchpad only):** no such crop logic
exists in the shipped app for Aadhaar or DL (only PAN has it), so new crop-band coordinates were
hand-built with Pillow for this test only, analogous to the app's real PAN bands but **never
iterated/validated the way the PAN bands were** — this matters for the accuracy results below.

**Fix 2 — `enable_thinking: False` via `chat_template_kwargs` (a request parameter, no code
change):** stops MiniCPM from burning its budget on reasoning. Combined with the crop canvas, this
is what finally got MiniCPM to produce an answer at all.

**Result:**

| Document | Cold start? | Latency | Finish reason |
|---|---|---|---|
| Aadhaar, canvas + nothink | Yes | 18s (8.5s read + 8.7s write) | `stop` (produces an answer) |
| DL, canvas + nothink | **No — server was reused warm, not a clean comparison** | 18s (8.8s read + 9.0s write) | `stop` |

Going from "never finishes" (83–109s, empty) to "finishes in 18s" is a genuine, significant
latency/functionality finding for MiniCPM. **The DL number above is not cold-started and should be
re-run before being treated as directly comparable to the Aadhaar number or to any other cold
figure in this document.**

**Accuracy, same two runs** — mixed, and not yet production-usable:

*Aadhaar (2 of 7 fields correct):*

| Field | Result | Cause |
|---|---|---|
| Aadhaar number | Last digit cut off | **My crop**, too tight on the right edge |
| Name | Wrong — picked the father's name instead of the cardholder's | Model error |
| DOB | Missed (null), despite being visible in the crop | Model error |
| Gender | Correct | — |
| Pincode | Cut short by one digit | **My crop**, same tight-edge issue |
| Address | Mostly right, minor typo | Partial |

*Driving Licence (2 of 5 fields correct; run not cold — see caveat above):*

| Field | Result | Cause |
|---|---|---|
| Licence number | Garbled, several digits wrong | Model error |
| Name | One letter off | Model error |
| DOB | Correct | — |
| Issue date | Correct | — |
| Validity date | Missed (null), despite being in the cropped region | Model error |
| Address | Truncated | **My crop**, bottom boundary too tight |

**Net assessment:** speed is no longer the blocker for MiniCPM on this configuration; accuracy
still is. Even setting aside the crop-boundary errors (fixable by widening margins, not yet done),
MiniCPM made 4 real model-side reading errors across 2 cards — worse than Qwen3-VL-2B's near-exact
results on the same two cards at the capped token budget (2.4 above).

### 2.6 Summary table — this phase, all models, cold, one card each

| Model | Document | Latency (optimized config) | Accuracy |
|---|---|---|---|
| Qwen3-VL-2B | PAN | 21s | High (see §2.4) |
| Qwen3-VL-2B | Aadhaar | 24s | High (see §2.4) |
| LFM2-VL-3B | Aadhaar | 40s | Not separately re-scored this phase |
| MiniCPM-V-4.6 | Aadhaar | 18s | 2/7 fields fully correct; 2 of the 5 errors are crop-boundary issues, not model |
| MiniCPM-V-4.6 | DL | 18s (not cold) | 2/5 fields fully correct; 1 of the 3 errors is a crop-boundary issue |

### 2.7 Open items from this phase

- DL canvas+nothink latency needs a clean cold re-run.
- Aadhaar/DL crop-band coordinates are first-draft and unrefined — widening the Aadhaar-number and
  pincode right-edge margin, and the DL address bottom margin, should fix 3 of the 5 accuracy
  errors found; not yet done.
- The "paper-based idea... peak_hit check on ~100 images" reference from an earlier pasted note was
  never clarified and remains unaddressed.
- None of this phase's findings have been implemented into the shipped app — everything above was
  scratchpad-only, consistent with the branch's working rules (no code changes without explicit
  sign-off).

---

## Cross-reference

- Qwen3-VL-2B general tuning history (core pinning, image-token-cap 128-vs-256 saga, GPU rejection):
  `QWEN3_VL_TWEAKS_LOG.md`.
- LFM2-VL-3B full latency study: `LFM2_VL_3B_LATENCY_FINDINGS.md`.
- MiniCPM-V-4.6 general findings and playbook: `MINICPM_LATENCY_FINDINGS.md`,
  `MINICPM_V_4.6_PLAYBOOK.md`.
- Per-phone (5.5GB unit) latency/accuracy tables, zram mechanism, memory-stall data:
  `ON_DEVICE_LATENCY_AND_ACCURACY_FINDINGS.md`.
- The uncommitted `-ctk q8_0 -ctv q8_0` change this document's Part 1 motivates:
  `mobile/idp-android/app/src/main/java/com/modeltesting/vlmtester/LlamaLaunch.kt`.
