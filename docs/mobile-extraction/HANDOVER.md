# VisionPsy-Nano / model_testing — handover (2026-09-29)

**⚠️ Written to tmpfs (`/tmp/.../scratchpad/`) because the laptop's root disk
(`/dev/nvme0n1p2`, mounted on `/`) is stuck in `emergency_ro` — every write to
the real project directory fails with EROFS. tmpfs does NOT survive a reboot.
If fixing the disk needs a restart, copy this file out first, e.g.:
`cp HANDOVER.md /home/kaarunya/Downloads/model_testing/` once `findmnt -T /`
no longer shows `emergency_ro`.**

## 1. The filesystem issue (blocking, top priority)

`findmnt -T /` shows `rw,relatime,emergency_ro` on the root ext4 volume —
the kernel auto-remounted read-only, almost certainly from a detected disk/fs
error. `dmesg`/`journalctl -k` couldn't be read from this session (permission
denied) to find the actual trigger. Next step is on the user's side:
`sudo dmesg | grep -iE "ext4|error|nvme"` or `journalctl -k -b`, then decide
whether an `fsck` is warranted before `sudo mount -o remount,rw /`. Everything
below that needs a local code edit or `git`/`gradle` command is blocked until
this is fixed. See memory `project_git_push_auth_gap.md` for a separate,
unrelated blocker (no GitHub credential helper in this environment).

## 2. MiniCPM-V 4.6 integration — in progress

**Goal:** add MiniCPM-V 4.6 (openbmb, GGUF from `ggml-org/MiniCPM-V-4.6-GGUF`)
as a second selectable model alongside Qwen3-VL-2B/PaddleOCR-VL/VisionPsy.
User confirmed it's genuinely ~1.3B total params (not the ~7-8B older
MiniCPM-V generations use) — `general.architecture=qwen35`, a compact hybrid
attention/SSM backbone, + a `minicpmv4_6` vision projector (window-attention +
downsample merger).

**Files already on-device** (pushed before the fs issue, still there —
confirmed via adb, phone storage unaffected by the laptop disk):
`/storage/emulated/0/Android/data/com.modeltesting.vlmtester/files/models/`
- `minicpm-v-4.6-q4_k_m.gguf` (529,101,536 bytes)
- `mmproj-minicpm-v-4.6-q8_0.gguf` (727,954,528 bytes)

Also present locally at
`/home/kaarunya/Downloads/model_testing/more_models/MiniCPM-V-4.6-GGUF/`
(same two files, downloaded from `ggml-org/MiniCPM-V-4.6-GGUF` on HF).

**Key finding: it already works with the app's EXISTING flags, no code
change needed.** Tested directly via `adb shell` running
`libllama-mtmd-cli.so` from the installed app's own `lib/arm64/` dir (app
already installed from an earlier build), using the app's real prompt/schema
files and exact CLI flags (grammar-constrained `-jf`, core-pinned threads,
CPU-only) — bypassing the app UI/local Kotlin entirely since local edits are
blocked. Findings:
- **No `--jinja` needed** — the CLI auto-matched the ChatML template baked
  into the GGUF's `tokenizer.chat_template` metadata without the flag. My
  earlier plan to add `isMiniCpmModel()` + gate `--jinja` on it turned out to
  be unnecessary — the model "just works" through the existing code path.
- **It's a reasoning model** — free-form (no grammar) it emits a
  `<think>...</think>` block before answering. Under grammar-constrained
  decoding (`-jf` + the real schema), that reasoning is fully suppressed —
  clean JSON, no leakage. Good sign, but only tested with grammar ON; never
  tested with grammar OFF against a real doc type.
- **Accuracy so far, matched against existing ground truth** (see
  `project_pan_full_token_cap_history.md` for the ground-truth table, and
  note its SAROJINI M correction):
  - UDAYRAJ SINGH card: 5/5 fields correct, byte-identical to Qwen3-VL-2B's
    already-validated Run 154 output.
  - MANIKANDAN SRIDHARAN card: 4/5 — `dob` came out `01/11/1999` instead of
    the correct `11/11/1999` (day-digit error), reproduced identically at
    both original and 1024-capped resolution (see below). `parent_name` came
    out `SRIDHARAN` (all-caps) here, vs. Qwen's `Sridharan` (mixed-case) —
    worth a real image zoom-in to settle which casing is actually printed on
    the card before trusting either as "correct" (same kind of transcription
    trap the SAROJINI M PAN-digit correction came from).
  - Only 2 of the 5 real ground-truthed cards tested so far. SOHRAAB DANISH,
    SAROJINI M, ANISH SANJIVA SHETTY, PRAYAGRAJ BEHERA not yet run against
    MiniCPM-V.

**Latency, real numbers (CPU-only, cold, no warmup):**
- Small pre-cropped image (575x347): ~62s wall including model load.
- MANIKANDAN card at original res (1080x1397): 1m56.1s.
- Same card resized to cap longest edge at 1024 (→792x1024): 1m32.0s —
  **~24s / ~21% faster, byte-identical output** (same fields, same single
  `dob` error). This is real evidence the Kotlin-side resize (see §3) is a
  genuine, additive win for this model specifically, not redundant with the
  native `MTMD_MAX_LONGEST_EDGE=768` env var.
- Roughly comparable ballpark to Qwen3-VL-2B's own cold-run numbers overall
  (36-58s range) despite MiniCPM's much smaller LM backbone — its mmproj
  file is actually *larger* than Qwen3-VL-2B's (728MB vs 445MB, both Q8_0),
  so the vision-encoder side likely dominates and offsets the smaller LM.

**Not yet done / blocked on the filesystem fix:**
- No code change has actually been applied to `MainActivity.kt` yet — the
  model is fully functional at the native-binary level but not yet wired
  through the app's UI/spinners in a verified end-to-end way (the spinners
  *should* auto-discover it with zero changes, since `populateModelSpinners()`
  just lists any `.gguf` in the models dir — but this hasn't been confirmed
  by actually tapping through the app UI yet).
- Remaining 3 ground-truthed cards untested against MiniCPM-V.
- Never tested MiniCPM-V against any other doc type (aadhaar, dl, passport,
  tt_*) — only pan_full so far.

## 3. Kotlin-side image resize (cap longest edge at 1024, keep aspect ratio)

User asked for camera images to be resized so the longest edge is capped at
1024px before reaching the model (NOT a literal 1024x1024 square — that would
distort/need letterboxing for rectangular ID photos, confirmed this
explicitly with the user before proceeding).

**Validated via the same native A/B test as above** (MANIKANDAN card,
original 1080x1397 vs resized 792x1024): ~21% latency win, zero accuracy
change. This directly disproves the hypothesis that the existing
`MTMD_MAX_LONGEST_EDGE=768` env var (see `project_android_gpu_cpu_config.md`
history / MainActivity.kt comments) already clamps MiniCPM-V's input
resolution — if it did, capping further at 1024 (larger than 768) would have
been a no-op, but it measurably wasn't. That env var's actual effect on Qwen
was also never confirmed either way (only confirmed for VisionPsy's idefics3
preprocessor, confirmed no-op for PaddleOCR) — worth testing Qwen with/without
it too, not just assuming it rides along harmlessly.

**Not yet implemented in code** (blocked by the fs issue). When unblocked,
the change is in `handlePickedFile()` in `MainActivity.kt`
(`/home/kaarunya/Downloads/model_testing/home/aiteam/model_testing/android-app/app/src/main/java/com/modeltesting/vlmtester/MainActivity.kt`,
~line 339-372): after decoding the picked bitmap, before the JPEG compress
step, add a resize step that scales down (never up) so
`max(width, height) <= 1024`, preserving aspect ratio — same math already
prototyped and tested via the `PIL` script in this session:
```python
scale = 1024 / max(w, h)
if scale < 1:
    new = im.resize((round(w*scale), round(h*scale)), Image.LANCZOS)
```
Kotlin equivalent would use `Bitmap.createScaledBitmap` with the same
never-upscale guard. Open question before wiring it in: should this REPLACE
the existing `MTMD_MAX_LONGEST_EDGE=768` env var's role, or run alongside it?
Given the 768 var's effect on Qwen/VisionPsy specifically hasn't been broken
by this test (only MiniCPM was tested), safest is probably to keep both for
now and re-test Qwen/VisionPsy accuracy after adding the Kotlin resize, in
case the interaction changes anything for them.

## 4. OpenMP core-pinning gap (separate, real latency lead)

Full detail in memory `project_android_gpu_cpu_config.md` (correction section
dated 2026-09-28). Short version: the `.so` binaries actually deployed to
`jniLibs/arm64-v8a/` were built with `GGML_OPENMP=ON`
(`build-android-vulkan`), and in that build, `ggml-cpu.c`'s
`ggml_threadpool_new_impl()` computes the `-Cr 4-7 --cpu-strict` cpumask but
**never calls `ggml_thread_apply_affinity()`** on it — that only happens in
the non-OpenMP branch. So the "pin to the 4 fast A78 cores" fix documented in
`MainActivity.kt`'s own comments doesn't actually take effect on the current
binary. Two fix options identified, neither applied yet (blocked by fs):
1. Swap `jniLibs` to the `build-android-opt` binary (`GGML_OPENMP=OFF`, real
   affinity, but no Vulkan/GPU — acceptable, GPU toggle is kept off anyway).
2. Keep the Vulkan-capable binary, add `OMP_PLACES`/`OMP_PROC_BIND` env vars
   via `pb.environment()` (same pattern as the existing image-token-cap env
   var), to get libomp's own affinity control instead.
Expected win if fixed: our own logged cap=256 runs already span 36.7s-57.9s
for supposedly-identical requests — fixing this should cluster latency near
the low end instead of swinging up to the high end. Not yet measured
before/after; this is inference from source code + existing variance data.

## 5. Everything else — current state, no open work

- **Git**: `qwen_implementation` branch pushed through commit `9d588de`
  ("Untrack .gradle/ build cache"). No pending commits beyond what's already
  on `origin`. See `project_git_push_auth_gap.md` — this environment has no
  credential helper/`gh` configured; pushing needs a one-off token passed
  transiently (never persisted to `.git/config`).
- **pan_full 128 vs 256 image-token cap**: resolved into a manual UI toggle
  (`switchPanFullHighCap`, default ON=256). Full history + the SAROJINI M
  ground-truth correction (real PAN is `LUQPS8083N`, not `LUQPS8003N`) is in
  `project_pan_full_token_cap_history.md`. Testing still incomplete at
  cap=128 for 3 of 5 ground-truthed cards — see that memory file for exactly
  which.
- **GPU/Vulkan**: confirmed net latency loss on this device's Mali-G68 MP5
  (no cooperative-matrix support) — keep the GPU toggle off. Not related to
  today's OpenMP finding (that's about CPU thread placement, not GPU
  offload) — don't conflate the two when explaining either to the user.

## 6. Suggested order once the filesystem is writable again

1. Re-verify `git status` is still clean (nothing should have changed on
   disk while it was read-only, but confirm before assuming).
2. Apply the Kotlin resize (§3) — smallest, most validated change.
3. Confirm MiniCPM-V shows up in the app's model spinners with zero other
   code changes (per `populateModelSpinners()`'s auto-discovery) — just an
   end-to-end UI smoke test, not a code change.
4. Pick one OpenMP fix option (§4) and actually measure before/after latency
   on a couple of already-known cards.
5. Resume the remaining 128-cap ground-truth testing (§5) if still relevant
   to current priorities — check with the user first, this was paused
   mid-way for the MiniCPM-V work.
