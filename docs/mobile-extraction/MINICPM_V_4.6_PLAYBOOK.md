# MiniCPM-V 4.6 (the "1B" model) — playbook: what worked, what failed, how to recreate it

Purpose: documented proof of what got the on-device PAN extraction from ~41-44 s to ~22 s per scan,
so the result can be recreated and the dead ends are not walked again. Scope: **this one model only.**
Written 2026-09-30. Raw evidence: `canvas_test_results/`. Longer narrative: `MINICPM_LATENCY_FINDINGS.md`.

Provenance tags used below: **[measured]** = timed/scored on the phone in the 2026-09-30 session;
**[handover]** = taken from `HANDOVER_MINICPM.md` / `HANDOVER.md` (earlier sessions, not re-run);
**[source]** = read from llama.cpp source; **[estimate]** = reasoning only, not run.

---

## 1. TL;DR

| | Before | After |
|---|---|---|
| Latency per PAN scan | ~40-44 s (app, whole photo) | **~22 s** (field canvas), 18.9-23.8 s over 25 runs [measured] |
| Vision work | 3 tiles = 186 image tokens | **1 tile = 64 image tokens** [measured] |
| What decided it | shrink the photo, let llama.cpp slice it | **cut the field regions ourselves, send one 448x448 image** |

Two changes, in order of size: (1) send **one 448x448 tile** instead of an image that always slices into
3 tiles (~-19 s); (2) cap the context with `-c 4096` (~-6 s on the old path). Accuracy is **not** solved
(see section 7): fields 92%, whole documents 60%, on clean crops only.

---

## 2. What we used

| Item | Value |
|---|---|
| Model | **MiniCPM-V 4.6** (openbmb), GGUF from `ggml-org/MiniCPM-V-4.6-GGUF`. ~1.3B parameters total per the handover ("1B" in team usage) |
| Language model file | `minicpm-v-4.6-q4_k_m.gguf`, 529,101,536 bytes, Q4_K_M, `general.architecture=qwen35` (hybrid attention/SSM), trained context 262,144 tokens |
| Vision projector | `mmproj-minicpm-v-4.6-q8_0.gguf`, 727,954,528 bytes, Q8_0, projector `minicpmv4_6`, `image_size=448`, patch 14, merge 4, 27 layers, embd 1152. **Larger than the LM** |
| Runtime | forked llama.cpp (`home/aiteam/model_testing/qvac-visionpsy-nano/llama-cpp-inference/llama.cpp-custom/`), binary `libllama-mtmd-cli.so` from the app's `lib/arm64/` |
| Device | Samsung Galaxy M35 5G, `SM-M356B`, Exynos 1380 (4x A78 fast = cores 4-7, 4x A55 slow = cores 0-3), Mali-G68, ~5.4 GB usable RAM |
| Mode | **CPU only**, 4 threads pinned to cores 4-7, flash attention on |
| Task | doc type `pan_full`, grammar-constrained JSON (`schema.json`), prompt `user_pan_full.txt`, greedy (`--temp 0`) |
| App | `com.modeltesting.vlmtester`, source `home/aiteam/model_testing/android-app/`, baseline commit `e1d3c73` (+ uncommitted changes) |

Model files live in `/sdcard/Android/data/com.modeltesting.vlmtester/files/models/` (not in the APK).

---

## 3. Where the time went (the diagnosis that drove everything)

Verbose run (`-v`), whole photo 560x350, 41.0 s wall [measured]:

| Stage | Time |
|---|---|
| Startup: fit pass, LM context, KV cache, projector load | ~6.8 s |
| **Vision encode: 3 tiles x ~7.3 s** | **21.9 s** |
| Image chunks decoded into the LM | 3.9 s |
| Text prefill + 65 output tokens (3.7 s) | 6.8 s |
| Teardown | 0.9 s |

Tokens: 60 + 63 + 63 = 186 image tokens; 317 prompt total; 65 generated; 382 processed.

**Why it is always 3 tiles** [source: `tools/mtmd/mtmd-image.cpp`, `get_slice_instructions` ~l.621,
`get_best_resize` ~l.769, `get_best_grid` ~l.847]:
- The 448 comes from the model file (`image_size`). If either side > 448 the image is sliced; if both <= 448 it is one tile.
- The grid never has a single slice, so slicing = **2 slices + 1 overview = 3 tiles minimum**. Latency is a step function: 1 tile or >= 3.
- Every tile is rescaled to ~448x448 area (aspect kept, sides rounded to multiples of 56) with upscaling allowed, so a small image is *enlarged*: a 433x560 input became `refined size 560x672`. Tile shapes like 392x504 are computed, not preset.
- One tile = 60-64 tokens = ~7-8 s of vision encode on this CPU. Nothing in the app can change this except the picture it sends.

---

## 4. What failed (do not retry)

| Approach | Result | Source |
|---|---|---|
| OpenMP pinning (`OMP_PLACES=4,5,6,7`, `OMP_PROC_BIND=close`) | No effect: 50.0/53.1 s baseline vs 50.4/53.2 s. `OMP_PLACES=cores` = **191 s**. 8 threads = 66.1 s | handover |
| GPU / Vulkan offload | Net latency loss on Mali-G68 (no cooperative matrix). Keep GPU switch off | old handover (not re-run) |
| `-fit off` | No gain: 45.3 s vs 44.3 s baseline | measured |
| `MTMD_MAX_LONGEST_EDGE`, image-token-cap toggle | **Inert for MiniCPM-V**: the minicpmv slicer reads neither (only idefics3 / Qwen do) | handover + source |
| Lowering the whole-photo resolution: 448 / 420 / 336 px | 1/4 cards correct each; 224 px wrong PAN digit; 112 px PAN unread. Failure = silent null/wrong `parent_name`, `dob` | handover |
| Raising it to 900 px | 67-73 s and 2/4, a *different* card fails: accuracy is not monotonic in resolution | handover |
| Crop to the card, then cap at 448 | 1/4: the slice threshold and the legibility threshold nearly coincide | handover |
| Whole photo at 560 px (the app's path) on real-shaped input | 2/4 cards fully right (harness), 3/5 fields on a real photo in the app (`SARJONIM M`, `MURIGESAN`), ~40 s | handover + measured |
| Front card only at 560 px | 39.6 s, 4/5 (PAN `LUQPS8033N`): no latency gain | measured |
| Two native-resolution tiles, split down the middle | 33.6 s, 4/5, but the split cut the PAN number and the model returned `SARJI1020C` | measured |
| **Tight** field boxes for the canvas | Clipped fields on 2 of 4 cards: PAN returned null; DOB **invented** (`01/01/1990`); father `SIR A III` | measured |
| "CLI vs server causes the accuracy loss" | Withdrawn; identical output at 560 px | handover |
| `HANDOVER.md` claims (no code change needed, `--jinja` needed, OpenMP fix) | Stale/disproven — read `HANDOVER_MINICPM.md` instead | handover |

Tooling traps: Android `mksh` does 32-bit arithmetic (a `date +%s%N` delta reports ~2 s for a 50 s run;
use `date +%s.%N` + `awk`); the server's prompt cache returns an identical repeat in ~7 s, so vary the
input when timing; the first request after a server start is a 56-63 s outlier; re-issue `adb forward`
after any reconnect.

---

## 5. What worked, and the shift in approach

### 5.1 Small win: cap the context (`-c`)

With no `-c`, llama.cpp used the model's 262,144-token context and reserved a 3 GB KV cache
(1.9 s just to allocate). Real use is ~250-400 tokens. Same card, byte-identical output [measured]:

| Config | Runs | Avg |
|---|---|---|
| baseline (`--no-warmup`) | 42.6, 46.0 s | 44.3 s |
| `-c 2048` | 36.4, 39.8 s | **38.1 s (-6.2 s)** |
| simulated camera frames, 4 cards, baseline -> `-c 2048` | 48.2 -> 42.5 s avg | -5.7 s |

Shipped as `-c 4096` (MiniCPM-V only; ~48 MiB KV): 40.3 s vs 52.6 s baseline on the heaviest test frame.
`--no-warmup` (skips llama.cpp's warm-up pass) was already worth -4.1 s, byte-identical [handover].

### 5.2 The big win: change what the model is given

**Shift in approach:** we stopped trying to tune the model's tiling (a fixed step function we cannot set)
and started **controlling the pixels before the model sees them**.

| | Old path | Canvas path |
|---|---|---|
| Who cuts the picture | llama.cpp's slicer, by geometry | our code, by field |
| Resampling | twice (shrink whole photo, then slicer resize/upscale) | once, ~1.0-1.35x, from original pixels |
| Tiles / image tokens | 3 / ~186 | **1 / 64** |
| Pixels spent on | QR code, photo, background, card back | only the four fields |

The canvas is one **exactly 448x448** image (one tile, so the slicer does not slice it) built from four
regions of the **front** of the card, expressed as fractions so they hold for any card size:

| Region | x0 | y0 | x1 | y1 |
|---|---|---|---|---|
| PAN number (+ title line) | 0.20 | 0.30 | 0.72 | 0.52 |
| Name | 0.00 | 0.52 | 0.50 | 0.68 |
| Father's name | 0.00 | 0.66 | 0.50 | 0.82 |
| DOB | 0.00 | 0.80 | 0.50 | 1.00 |

Each band is scaled to `min(448 / band_width, 1.35)`, stacked top to bottom with a 3 px gap on a white
448x448 background, saved as JPEG q95. The first, tight version of these boxes failed (section 4);
**generous margins were required** (the canvas only uses ~330-430 of 448 px of height, so there is room).

Measured for the canvas [measured, verbose run]: encode starts ~3.0 s, **1 encode 7.5 s**, decode into LM
1.3 s, prompt 189 tokens (64 image + ~125 text) prefilled in 11.7 s, 66 tokens generated in 3.7 s,
17.8 s inside llama.cpp, 19.3 s wall.

---

## 6. How to recreate it

### 6.1 In the app (already implemented, uncommitted)

- `MainActivity.kt`: `panFrontBands`, `buildFieldCanvasFile()` (near `downscaleForMiniCpm()`), and the
  `when` in the single-pass branch of `runExtraction()` that picks it. `-c 4096` is in `runOnePass()` and
  `ensureServerRunning()`, gated by `isMiniCpmModel()`.
- UI: switch **"pan_full + MiniCPM-V: field canvas (experimental)"**, default **off**. Doc type
  `pan_full`, model+mmproj = MiniCPM-V 4.6, GPU off, server mode off.
- **Input must be the front of the card only** (crop first). The bands are fractions of the whole picture.
- Each run logs `prep=canvas Xs total_incl_prep=Ys`; the canvas sent is saved as `last_canvas.jpg` next
  to `run_log.txt`.
- Build: `JAVA_HOME=~/toolchain/jdk-17* ANDROID_HOME=~/toolchain/android-sdk ~/toolchain/gradle-8.7/bin/gradle --no-daemon :app:assembleDebug`
  from `android-app/` (`local.properties` with `sdk.dir`; the 11 `.so` files must be in
  `app/src/main/jniLibs/arm64-v8a/`, copied from `app_build/lib/arm64-v8a/`). `adb install -r` works
  only with a build signed by the same key; this laptop's key differs from the original machine's.

### 6.2 Without the app (the test harness that produced the numbers)

Binaries and scripts on the phone: `/data/local/tmp/mini/` (`.so` files) and `/data/local/tmp/mini/mt/`
(`run.sh`, `pan_full.json`, `system.txt`, `user_pan_full.txt`, canvases). `run.sh` (exact flags):

```sh
LD_LIBRARY_PATH=/data/local/tmp/mini LLAMA_ARG_IMAGE_MAX_TOKENS=256 \
/data/local/tmp/mini/libllama-mtmd-cli.so \
  -m $M/minicpm-v-4.6-q4_k_m.gguf --mmproj $M/mmproj-minicpm-v-4.6-q8_0.gguf \
  -jf pan_full.json --image "$IMG" -sys system.txt -p "$(cat user_pan_full.txt)" \
  -n 512 --temp 0 --repeat-penalty 1.1 -t 4 -tb 4 \
  -Cr 4-7 -Crb 4-7 --cpu-strict 1 --cpu-strict-batch 1 -fa on \
  -ngl 0 --no-mmproj-offload --no-op-offload  --no-warmup -c 4096
```

Add `-v` for the stage timeline. Building a canvas on a laptop (PIL), from a front-card image `card`:

```python
bands=[(0.20,0.30,0.72,0.52),(0.00,0.52,0.50,0.68),(0.00,0.66,0.50,0.82),(0.00,0.80,0.50,1.00)]
cv=Image.new('RGB',(448,448),(255,255,255)); y=0
for x0,y0,x1,y1 in bands:
    W,H=card.size
    c=card.crop((round(x0*W),round(y0*H),round(x1*W),round(y1*H)))
    s=min(448/c.width,1.35)
    c=c.resize((round(c.width*s),round(c.height*s)),Image.LANCZOS)
    cv.paste(c,(0,y)); y+=c.height+3
cv.save('canvas.jpg',quality=95)
```

Expected outputs (identical every run, greedy): see `canvas_test_results/runs.csv`. Push canvases with
`adb push`, run `./run.sh canvas.jpg 512 label --no-warmup -c 4096`.

---

## 7. Final success metrics

Canvas, 5 cards x 5 repeats = 25 runs, harness [measured; raw data in `canvas_test_results/`]:

| Metric | Result |
|---|---|
| **Latency** | **22.4 s avg**, 18.9-23.8 s; in-app check 21.8 s and 22.2 s (canvas build 0.01-0.02 s) |
| Image tokens / prompt / output | 64 / 189 / 66 (vs 186 / 317 / 65) |
| Fields correct | **115/125 = 92%** |
| Documents fully correct | **15/25 = 60%** (3 of 5 cards) |
| PAN number | 4/5 cards (80%) |
| Name, father's name, relation, DOB | 4/5, 5/5, 5/5, 5/5 |
| Same 4 cards, old whole-photo path | 42.5 s avg, 10/20 fields (50%), 1/4 documents |

Every error repeats identically (greedy decoding): card c PAN `FCLPM0017F` (printed `FCLPM0001F`), card e
name `SAROJIN M` (printed `SAROJINI M`). 25 runs = 5 distinct results.

## 8. Known gaps — read before relying on these numbers

1. **Ground truth was read by eye.** No verified answer key exists.
2. **Best-case inputs**: tight front-card crops, no live camera photos. **Finding and straightening the
   card is not built**; without it the fractional bands land on the wrong text and fail silently
   (nulls, or invented values like the DOB above).
3. **The bands were widened after seeing failures**, so the 92% is partly fitted to these five cards.
4. **Tiny sample**: 95% range roughly 23-88% (documents) and 75-98% (fields).
5. **PAN digit slips look valid** and PAN has no checksum. A second independent read is the open fix
   (idea: reuse the ML Kit OCR from `docs/Gemma-OCR-Developer-Handoff.docx` — untested here; also for
   anchoring the bands on the printed labels).
6. One template for standard PAN fronts only; e-PAN/PVC/older layouts, Aadhaar, DL, passport untested.
7. `-c 4096` untested for `tt_*`/`dl` prompts (expected to fit).
8. Server mode was not re-timed in the app; the handover put a warm server at ~48-50 s vs ~54.5 s for the
   old CLI path, i.e. only ~5-7 s better, and it pays a 24.6 s cold start.

Suggested gate before adopting: >= 90% of documents fully correct on 30-50 real phone photos with
ground truth checked against the physical cards, no invented values, < 30 s including card finding.
