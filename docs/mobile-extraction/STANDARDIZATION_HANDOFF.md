# Mobile extraction standardization: handoff

Status on 2026-10-22, branch `mobile_standards`. **Nothing is committed or pushed.** All changes are in the working tree.

This page is the entry point. It says what was done, how to check it, what is still open, and one performance finding from the phone that needs a decision. The detail lives in the documents it links to.

## 1. Scope

Standardize the existing on-device document extraction (`mobile/idp-android`, Aadhaar and PAN, Kotlin and llama.cpp) to the IDP standards in `standards_docs/`.

- In scope: `mobile/idp-android/`, `docs/mobile-extraction/`, `docs/adr/`. `research/` is read-only context.
- Out of scope: new features, backend work, RAM-tier model selection (front-end team), the Compose/Hilt/Room stack (the team lead has not agreed it), and the other teams' folders (`services/conversion-service`, `services/conversions`, `services/ocr`, `mobile/idox-mobile`).
- PaddleOCR-VL support was removed at the owner's request. Its findings docs and logs stay as history.
- Behavior and measured values were moved, not changed. Rule-by-rule status: [STANDARDS_COMPLIANCE.md](STANDARDS_COMPLIANCE.md).

## 2. What was done

| Area | What changed | Standards touched |
|---|---|---|
| Logic split out of the activity | The rules, parsing, request-building and launch-argument code moved from `MainActivity.kt` into 19 small pure Kotlin files (`*Rules`, `OutputParser`, `ModelProfiles`, `ExtractionPlan`, `ImageSizing`, `LlamaLaunch`, `LlamaRequest`, `ModelFiles`, `RunLog`, `ByteFormat`, `ProcessStats`, `GpuSupport`, `PromptFile`, `ModelRegistry`) | PY-11, DOM-01, DOM-05, PKG-01, LLM-01 (partly) |
| Tests | 227 JVM unit tests, planted-bug checks on each group, JaCoCo coverage (99.9% of lines, 90.1% of branches of the extracted logic) | IDP-04 3.9, Ch. 21, TST-10 |
| Lint and gates | ktlint 1.3.1, detekt 1.23.6 (limits borrowed from the Python Standard: complexity 10, 50 lines, 7 parameters, 400 lines), Android Lint, `scripts/check.sh` | Ch. 27, ADR 0003 |
| Prompts | 15 prompts are now `<name>.v1.md` with front matter (model, temperature, schema); the run-log header records the prompt version; a test pins the SHA-256 of every body | LLM-02, ML-04, IDP-03 |
| Model registry | `assets/models.yaml` (5 models) with a strict reader; the run log records registry name, version and checksum | ML-01, ML-02, ML-04 |
| Privacy and security | Cleartext HTTP limited to the loopback address; backup and device transfer excluded; run data kept in private storage; R8 minification on release | IDP-04 3.8, CFG-03 |
| GPU switch | The "Use GPU (Vulkan)" switch is disabled when the build has no Vulkan library, so it can no longer silently run on the CPU | ML-10 |
| Resources | 30 hard-coded texts moved to `strings.xml`; content descriptions added | IDP-04 3.7 (Lint) |
| Build | `native/build_llama_dotprod.sh` pinned to llama.cpp commit `000bee5`, fixed NDK and CMake versions, no machine-specific paths | BLD-02, DPL-01/02 |
| Decisions and docs | ADRs 0001 to 0006 (all Proposed, none approved), compliance matrix, architecture note, phone checklist, toolchain versions, a repo-root `CLAUDE.md` | Java Std 33.6, 34.1 |

Files and folders: `mobile/idp-android/app/src/main/java/com/modeltesting/vlmtester/` (the new files and `README.md`), `app/src/test/`, `app/src/main/assets/prompts/` and `models.yaml`, `scripts/check.sh`, `config/detekt/`, `.editorconfig`, `native/build_llama_dotprod.sh`, `docs/adr/`, `docs/mobile-extraction/`.

## 3. How to check it

From `mobile/idp-android`:

```
scripts/check.sh
```

It runs ktlint, detekt, the unit tests with coverage floors (85% lines, 70% branches), Android Lint, debug and release builds, and fails if a native library, model, APK or signing key is tracked. JDK 17 and the Android SDK live under `~/Library/Android` (see [TOOLCHAIN_AND_VERSIONS.md](TOOLCHAIN_AND_VERSIONS.md)).

## 4. Phone verification

Run on a Galaxy M35 (Android 16) on 2026-10-22:

- **Confirmed:** the app installs, lists the models, loads them, runs a scan and writes the new run-log header with the registry and prompt-version fields. The GPU switch is disabled as intended.
- **Found:** app folders created from the shell need `chmod` before the app can read the models on Android 16; the old and new builds use different signing keys, so they cannot be installed over each other.
- **Not done:** the 21 checks in [PHONE_CHECKLIST.md](PHONE_CHECKLIST.md) have not been run systematically.

## 5. Performance finding: `libmtmd.so` from the pinned build is about 3x slower

**Symptom.** LFM2 `pan_full` took 85 to 95+ s with the standardized app, against 24 to 36 s with the build that was on the phone before (your old log and today's runs of the old APK: 36 s and 36.9 s).

**What it is not.**
- Not the phone: it was cool (thermal 0 to 1), with about 3 GB of RAM free and no other process running.
- Not the Kotlin code: the new Kotlin code with the old APK's native libraries ran in 32.4 s.
- Not the launch settings: the cap (`LLAMA_ARG_IMAGE_MAX_TOKENS=128`), prompt, grammar, threads and context were the same.

**What it is.** The native libraries that `native/build_llama_dotprod.sh` produces ("dist_dot") are slower. Swapping libraries one at a time into the old set, with the app's exact command and a 50 s cap per run:

| Libraries | Time |
|---|---|
| Old set | 16 s |
| New set | killed at 50 s |
| Old set with new `libllama` | 17 s |
| Old set with new `libllama-common` | 22 s |
| Old set with new `libmtmd` | killed at 50 s |

The new `libmtmd.so` is the cause. Both versions read the 128-token cap, but the old one shrinks the card to one 448x256 image (112 image tokens, encoded in 3.4 s), and the new one splits it into 15 tiles of 256 tokens (about 3,800 image tokens, 11 s per tile). The compiled code is nearly identical (257k instructions each), so this looks like a difference in the multimodal source, not in the compiler.

**Why it tiles (confirmed by a size test).** The two sets were built on different machines from different source folders: the old one in `/home/kaarunya/toolchain/build_check2/src` (Linux, BOLT and MLGO compiler build), the new one in `/Users/impacto/Downloads/app_build/llama_dotprod/src` (macOS). Both are stamped `000bee5`. In llama.cpp at `000bee5`, the LFM2 image preprocessor tiles when the rounded image area exceeds twice the pixel budget of the token cap (upstream PR #27057, 2026-08-18). At a cap of 128 tokens the budget is 131,072 px, so anything above about 262,000 px tiles; the 860x538 PAN card (about 468,000 px) does. Tests with the app's exact flags, 50 s cap, same card resized:

| Libraries | Image | Image tokens sent | Time |
|---|---|---|---|
| New | 860x538 (the app's size) | 15 tiles, about 3,800 | 85 to 95+ s |
| New | 500x300 | 1 image, 112 | 17 s |
| Old | 1400x875 | 1 image, 112 | 14 s |

**Not yet known.** Why the old library never tiles. It did not tile a 1400x875 card either, so it does not follow the older "wider than 1,024 px" rule that appears in the research copy of llama.cpp. It may be patched or built from different source; the old build folder is not available to check.

**Other models are not affected.** One card, app flags, 50 s cap, new libraries against old:

| Model and path | New libraries | Old libraries |
|---|---|---|
| Qwen3-VL 2B, full card | 25 s | 25 s |
| MiniCPM-V 4.6, full card | 27 s | 27 s |
| MiniCPM-V 4.6, field canvas (448x448, the app's path) | 16 s | 17 s |
| LFM2-VL 3B, full card | 85 to 95+ s | 16 to 36 s |

Upstream, Qwen3-VL uses a dynamic-size preprocessor that does not tile; the tiling rule is specific to LFM2. MiniCPM-V slices large images, but its slicing was identical in both builds. Only LFM2 is affected.

**Options (each needs the team lead).**
- A. Pin an upstream commit from before 2026-08-18 that still supports the other models. Not tested.
- B. Keep the pinned commit and resize the LFM2 `pan_full` input in Kotlin. Tested from the command line, not in the app (see below).
- C. Keep the old `libmtmd.so` inside the new set. Tested on one card: 22 s with the same output as the old set. Its source is not documented, so it cannot be rebuilt, which conflicts with BLD-02 and DPL-01/02.

**Test of option B (LFM2, new libraries, five cards, app flags and grammar, 50 s cap, nothing killed).** Card a at 500 px, three reruns: 24, 26 and 25 s, all correct (an earlier 51 s kill was a one-off). Size sweep over all five cards:

| Longest edge | Time per card | Result |
|---|---|---|
| 400 px | 23 to 27 s | worse: card a PAN misread, card c case changed |
| 500 px | 22 to 26 s | correct on cards a to d; card e relation wrong (also wrong with the old libraries) |
| 600 px | 24 to 26 s | the same fields as 500 px, but closer to the tiling threshold (about 262,000 px) |

Recommended size if B is chosen: 500 px longest edge. The old library read card b's date of birth as `02/02/2004`; the card says `03/02/2004`, which the 500 px run got right.

**Accuracy check, all three models (new libraries, the app's input per model, five cards, truth read from the card images).**

| Model, input | Fields correct (of 25) | Cards fully correct | Time per card | Errors |
|---|---|---|---|---|
| Qwen3-VL 2B, 480 px resize | 25 | 5/5 | 19 to 20 s | none |
| LFM2-VL 3B, 500 px resize | 24 | 4/5 | 26 to 29 s | card e relation `mother` (card says Father's Name) |
| MiniCPM-V 4.6, field canvas | 22 | 2/5 | 17 to 18 s | card c PAN `FCLPM0017F`, card d PAN `AJTPI4244M`, card e name `SAROJIN M` |

Limits: five cards, one run each, command line only (not the app), the app's resize and canvas code rebuilt outside the app (sips and a small Java program). No camera photos were tested.

**Consequence.** The pinned build script does not reproduce the old app's LFM2 speed unless the input is resized (B) or the old `libmtmd` is used (C). Until the team lead decides, do not call the dot-product build from that script "the build". It belongs in the build ADR (0001) and in the compliance matrix (BLD-02).

## 6. Open items

Needs the team lead:

1. Approve or change the six ADRs (all Proposed).
2. Min SDK 28 against the standard's 26 (the native libraries are built for android-28).
3. Whether the on-device model stays, against the standards' direction for model calls through Python.
4. Whether the Compose, Hilt and Room stack is in scope.
5. Run-log content: it holds extracted fields on purpose (ADR 0002 row 4).
6. Choose A, B or C in section 5. Recommendation: B at 500 px, with the accuracy re-checked on more cards and in the app; C only as a fallback.

Needs the team or more work:

7. Model registry gaps: the 4B model has no file name, versions are `unversioned`, 2 checksums are missing.
8. Evaluation set and CI (ML-11, ML-12, IDP-03). Nothing runs automatically.
9. No timeout on the one-shot CLI path (LLM-05).
10. `MainActivity.kt` is still 1,779 lines (limit 400) and is excluded from ktlint and detekt. Splitting out the process start, HTTP calls and extraction flow is held until the phone checks can prove the move.
11. The native libraries are not 16 KB page-aligned (an Android 16 and Play compatibility warning). The usual remedy, a flexible-page-size build flag or a newer NDK, is untested.
12. Signing for release (the standards do not describe Android signing).

Note: the counts in [STANDARDS_COMPLIANCE.md](STANDARDS_COMPLIANCE.md) are from earlier in the day (226 tests, `MainActivity.kt` about 2,000 lines); the current numbers are 227 tests and 1,779 lines.

## 7. Working agreements for this branch

- No commit, push, PR or merge until the owner says so. Merge to `dev` happens once, at the end.
- Move code without changing behavior or any measured value; keep the measurement comments with their constants.
- Before calling a step done: `scripts/check.sh`, a diff of `MainActivity.kt` against a backup, and a planted bug in a scratch copy to show a new test can fail.
- Never silently add a dependency or tool, change a threshold, prompt, output field or error code, skip a test, or add a lint suppression without a code and a reason.
- Native libraries, models, APKs and signing keys never go in git.

## 8. State of the phone and the laptop

- Phone: a scratchpad build with the new Kotlin code and the old APK's native libraries is installed (not the repo's pinned build); the six models are in its app folder, with a backup in `/sdcard/Download/idp_backup_2026-10-22/models`. My test files in `/data/local/tmp` were deleted.
- Laptop: the old APK, its run log and the unpacked libraries are kept outside the repo in the session scratchpad. They are not in git and are not needed to build the app.
- Working tree: the ignored `app/src/main/jniLibs/arm64-v8a/libomp.so` holds the old APK's copy from an early test; the original `dist_dot` copy is saved in the session scratchpad. Nothing under `jniLibs` is tracked by git.
