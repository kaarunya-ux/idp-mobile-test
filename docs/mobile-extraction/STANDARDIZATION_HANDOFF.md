# Mobile extraction standardization: handoff

> Starting a new session (human or AI)? Read [AI_CONTEXT.md](AI_CONTEXT.md) first: the project in one page, what was found and changed, open problems, where things are, how to build and test, and the traps.

Status updated 2026-10-26, branch `mobile_standards`. The owner committed and pushed `d3a8721` and `5cb6f57`; **everything since then (about 25 files) is uncommitted**, in the working tree. Nothing is committed or pushed without the owner saying so.

This page is the entry point. It says what was done, how to check it, what is still open, and one performance finding from the phone that needs a decision. The detail lives in the documents it links to.

## 1. Scope

Standardize the existing on-device document extraction (`mobile/idp-android`, Aadhaar and PAN, Kotlin and llama.cpp) to the IDP standards in `standards_docs/`.

- In scope: `mobile/idp-android/`, `docs/mobile-extraction/`, `mobile/idp-android/docs/adr/`. `research/` is read-only context.
- Out of scope: new features, backend work, RAM-tier model selection (front-end team), the Compose/Hilt/Room stack (the team lead has not agreed it), and the other teams' folders (`services/conversion-service`, `services/conversions`, `services/extraction-service`, `mobile/idox-mobile`).
- PaddleOCR-VL support was removed at the owner's request. Its findings docs and logs stay as history.
- Behavior and measured values were moved, not changed. Rule-by-rule status: [STANDARDS_COMPLIANCE.md](STANDARDS_COMPLIANCE.md).

## 2. What was done

| Area | What changed | Standards touched |
|---|---|---|
| Logic split out of the activity (stage 1 of the `MainActivity` split done 2026-10-26: `TtRules`, `ValidationReports`; phone check passed: Runs 51 to 56 (licence, passport, Aadhaar, PAN) gave the same answers and the same check lines as before the move; stage 2 done the same day: `ProcessMonitor` (live stats sampling, process ids) and `RunCounter`, 6 tests; phone check passed in server mode (Run 57: live stats ticked, run number continued from 56), one-shot path checked with Run 58; stage 3 done: `ImagePreparation` (resizes and the PAN field canvas, needs Android Bitmap so no unit tests; coverage figure now includes it); phone check: the field canvas, the MiniCPM-V resize and the Qwen document resize files from the new build are byte-identical to the old build's for the same cards (Runs 63 to 67); the Qwen PAN and LFM2 resizes call the same code with another size and were not re-compared; next `CliScan`, `ModelServer`, `ExtractionFlow`, each with a phone check) | The rules, parsing, request-building and launch-argument code moved from `MainActivity.kt` into 19 small pure Kotlin files (`*Rules`, `OutputParser`, `ModelProfiles`, `ExtractionPlan`, `ImageSizing`, `LlamaLaunch`, `LlamaRequest`, `ModelFiles`, `RunLog`, `ByteFormat`, `ProcessStats`, `GpuSupport`, `PromptFile`, `ModelRegistry`) | PY-11, DOM-01, DOM-05, PKG-01, LLM-01 (partly) |
| Tests | 278 JVM unit tests, planted-bug checks on each group, JaCoCo coverage (91.7% of lines, 86.6% of branches of the extracted logic) | IDP-04 3.9, Ch. 21, TST-10 |
| Lint and gates | ktlint 1.3.1, detekt 1.23.6 (limits borrowed from the Python Standard: complexity 10, 50 lines, 7 parameters, 400 lines), Android Lint, `scripts/check.sh` | Ch. 27, ADR 0003 |
| Prompts | 18 prompts are now `<name>.v1.md` with front matter (model, temperature, schema); the run-log header records the prompt version; a test pins the SHA-256 of every body | LLM-02, ML-04, IDP-03 |
| Model registry | `assets/models.yaml` (5 models) with a strict reader; the run log records registry name, version and checksum | ML-01, ML-02, ML-04 |
| Privacy and security | Cleartext HTTP limited to the loopback address; backup and device transfer excluded; run data kept in private storage; R8 minification on release | IDP-04 3.8, CFG-03 |
| GPU switch | The "Use GPU (Vulkan)" switch is disabled when the build has no Vulkan library, so it can no longer silently run on the CPU | ML-10 |
| Resources | 30 hard-coded texts moved to `strings.xml`; content descriptions added | IDP-04 3.7 (Lint) |
| Build | `native/build_llama_dotprod.sh` pinned to llama.cpp commit `000bee5`, fixed NDK and CMake versions, no machine-specific paths | BLD-02, DPL-01/02 |
| LFM2 input size | LFM2-VL gets a shrunk image: PAN 500 px, Aadhaar, DL and passport 560 px (`ModelProfiles`, `ExtractionPlan.Plan.maxEdge`, `MainActivity.downscaleForLfm2`). Unit-tested, not yet run on a phone (section 5) | ML-07, ML-05, ADR 0007 |
| Compact prompts | DL, passport and Aadhaar get a short prompt, no system prompt and a small grammar (3 prompt files, 3 grammars); the checks grade only the fields asked for (`ModelProfiles.useCompactDocument`, `Plan.compact`, `compact` flag in `DlRules`, `PassportRules`, `AadhaarRules`). Fewer fields come back (ADR 0008) | LLM-02, LLM-03, ML-07 |
| Clean result panel | After a scan the output panel shows one `Label: value` line per extracted field plus the verdict and warnings (`ResultDisplay`, new, 3 tests); the run details (processor, mode, model, threads, image-token limit and an estimate of the image tokens, prompt version, image size, server warm or cold, tokens, read/write/total time) move to a separate "Run details" card under the live stats (`RunInfoDisplay`, 2 tests); everything is still saved to `run_log.txt` unchanged. Passport check also flags expiry not after issue (`PassportRules`) | IDP-04, ML-10 |
| Qwen input size | Qwen3-VL gets DL, passport and Aadhaar images at 560 px at most (`QWEN_DOCUMENT_MAX_EDGE`, `ImagePrep.QWEN_DOC`); PAN unchanged (ADR 0009) | ML-05, ML-07 |
| Rule fixes | A real calendar check on every date (31 September is rejected), a DL number that starts with the label `DL No` is flagged, the DL issue year may go back to 1950 | LLM-03 |
| One model in memory | Turning server mode off, or starting a one-shot scan, stops the warm server so two copies of the model are never loaded (`MainActivity`; proven only by the logs so far, not on a phone) | ML-03 |
| Decisions and docs | ADRs 0001 to 0009 (all Proposed, none approved), compliance matrix, architecture note, phone checklist, toolchain versions, a repo-root `CLAUDE.md` | Java Std 33.6, 34.1 |

Files and folders: `mobile/idp-android/app/src/main/java/com/modeltesting/vlmtester/` (the new files and `README.md`), `app/src/test/`, `app/src/main/assets/prompts/` and `models.yaml`, `scripts/check.sh`, `config/detekt/`, `.editorconfig`, `native/build_llama_dotprod.sh`, `mobile/idp-android/docs/adr/`, `docs/mobile-extraction/`.

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
| MiniCPM-V 4.6, field canvas | 22 | 2/5 | 17 to 18 s | card c and card d: PAN misread; card e: name misread |

Limits: five cards, one run each, command line only (not the app), the app's resize and canvas code rebuilt outside the app (sips and a small Java program). No camera photos were tested.

**Change made on 2026-10-23 (option B, in the working tree, not committed).** The app now shrinks the image before LFM2 reads it: PAN to 500 px, Aadhaar, DL and passport to 560 px. Before the change the app capped LFM2 at 1,280 px, which left an 860 px PAN card unshrunk (the old code comment assumed the model shrinks the image itself, which was true of the old libraries and is not true of the new `libmtmd`). Code: `ModelProfiles.LFM2_PAN_MAX_EDGE`, `LFM2_DOCUMENT_MAX_EDGE`, `useLfm2DocumentResize`; `ExtractionPlan.Plan.maxEdge`; `MainActivity.downscaleForLfm2(imageFile, longestEdge)`. Tests: 231 pass, and three planted bugs (wrong set, wrong edge, ignored resize) each fail the new tests. Decision record: [ADR 0007](../../mobile/idp-android/docs/adr/0007-shrink-lfm2-input-images.md). **Not done:** the app's own resize has not been run on a phone (the numbers above came from images resized with `sips` and a command-line run), and the accuracy cost for LFM2 against full size is not measured. Qwen3-VL and MiniCPM-V are unchanged.

**Consequence.** The pinned build script does not reproduce the old app's LFM2 speed unless the input is resized (B, now in the code) or the old `libmtmd` is used (C). Until the team lead decides, do not call the dot-product build from that script "the build". It belongs in the build ADR (0001) and in the compliance matrix (BLD-02).

**Update 2026-10-26 (what happened after option B).**

- *Compact prompts (ADR 0008).* The full DL prompt was about 800 tokens plus a 146-token answer: 63.7 s in the app. The short prompt gives 24.7 to 37.7 s. Fewer fields come back (list in the ADR).
- *Qwen resize (ADR 0009).* Test-server sweep, Qwen3-VL 2B, 5 or 6 cards per cell: 560 px gave DL 19/25 (15 to 19 s), passport 28/34 (22 to 24 s), Aadhaar 19/24 (11 to 17 s); 400 px gave 14/25, 19/34, 17/24 and was dropped; full size gave DL 20/20 (29 to 75 s), passport 33/34 (63 to 74 s), Aadhaar 22/24 (21 to 87 s). The app now uses 560 px for Qwen on DL, passport and, at the owner's request, Aadhaar (the sweep used the compact Aadhaar prompt, not Qwen's own Aadhaar profile). In-app Qwen Aadhaar run at 560 px (2026-10-26, Run 23, `aa_1`): 28.2 s with a cold server start, 653 prompt tokens; the number (6060 for 6606), name (Megha for Meghna), address end and PIN (546106 for 845106) were wrong, and the checksum rule marked it LOW. Smoke test on the test server with Qwen's own Aadhaar profile, three cards (`aa_1`, `aa_2`, `aa_3`), warm server: 560 px 23 to 37 s, 10/12 core fields; 700 px 29 to 45 s, 10/12; 800 px 35 to 59 s, 11/12. 700 px read the `aa_1` address and PIN right (845106) where 560 px did not, for 6 to 10 s more. The front-only card `aa_3` gets an invented address and PIN at every size (a prompt or grammar problem, not an image-size one). Three cards, one run each, so only indicative. **The owner decided on 2026-10-26 to keep 560 px.**
- *In-app results.* LFM2: DL 16/20 (4 cards, 25 to 38 s), passport 13/14 (31 to 36 s), Aadhaar 7/8 (29 to 43 s, one-shot), PAN 4/5 (29 s, the card was 2127x1282 and was sent at 500 px). Qwen: PAN 10/10 (14 to 18 s), DL 10/10 (34 s and 74.8 s, full size, before the resize), passport 6/7 (62 s, full size). The answer for a card was the same in the app and on the test server in 10 of 11 comparisons.
- *Memory.* LFM2 needs about 2.3 GB resident. On the 5.5 GB phone it stalled three times (scans over 100 s that never logged): memory stall 27 to 54%, `kswapd0` at 97 to 100%, the model holding only 0.6 to 0.9 GB in RAM, zram using 2.4 GB of RAM. A leftover warm server plus a one-shot scan put two copies of the model in memory; that is fixed (see the row above). Qwen stayed in RAM (2.1 GB resident, stall 0%).
- *The app's check is lenient.* It says HIGH when every field is filled, even with a missing passport surname or a licence number cut short. Not fixed.
- *In-app results.* In-app runs 20 to 34 (2026-10-26, Galaxy M35, warm server unless noted, images at 560 px, scored against `data/india_docs/*/truth.md`): **Qwen3-VL 2B** DL 7/10 fields (Delhi 5/5 in 21.6 s; Kerala 2/5 in 20.7 s: issue and validity dates wrong and the licence prefix read as DL for KL, which was first scored as 3/5 by mistake; at full size it was 5/5 in 74.8 s), passport 12/14 (28.9 and 31.6 s; one missing surname, one place of birth "Bharat"), Aadhaar 11/12 on three more cards (17 to 47 s; "Bajal" for "Bajaj", PIN 780001 for 786001, invented address and PIN on a front-only card, which the check marked LOW). **MiniCPM-V 4.6** (first in-app runs) Aadhaar 8/8 on two cards (about 20 s, read "Meghna" and 6606 correctly, which Qwen never did), DL 8/10 (23.0 and 26.6 s; a licence number with digits lost, a validity day off by one), passport 8/14 (27.4 and 30.1 s; names garbled, issue year 2011 for 2021, expiry 2014 for 2033). One DL card was scanned by mistake as Aadhaar: MiniCPM invented the number 1234 5678 9012 and the checksum rule marked it LOW. The app's HIGH label means all fields were filled, not correct: Runs 21, 31, 33 and 34 had wrong values and were HIGH. Indicative only (7 to 10 cards per model, one run each). Passport expiry-before-issue (Run 34) is now flagged by a new rule in `PassportRules`.
- *Later in-app results.* In-app runs 35 to 67 (2026-10-26, same phone, 560 px): **LFM2-VL** Aadhaar 7/8 on two cards (Runs 44, 45; one-shot 31 and 40 s; one name lost its last letter); passport Attarwala 7/7 (Runs 47, 49, 51) and Rathore 6/7 (Runs 48, 50, 52, 61), where it reads the **father's name as the holder's** every time and the check says HIGH; one-shot 49 s against 36 to 43 s with the server, with memory stalls on this 5.5 GB phone (the model is about 2.3 GB). **Qwen PAN** (480 px profile) 5/5 on `card_a` and `card_b`, 13.9 to 21.8 s (Runs 56 to 59). **MiniCPM-V PAN** with the field canvas 5/5 in 20.3 s (Runs 35, 65). **Qwen Aadhaar** `aa_2` took 38.8 to 47.3 s (Run 67: 44.9 s plus an 8.2 s server start): its own profile sends 743 prompt tokens and writes 146 (full address and PIN), against 262 and 60 for the compact prompt; about 25 s reading and 20 s writing. **MiniCPM-V passport, same image, two modes:** `pp_4` (Solanki) was garbled in server mode (Run 63, 5/7) and 7/7 in one-shot (Run 66); not yet explained. `pp_5` (glare) was LOW because of an impossible issue year (Run 62), which the rules caught. **Image before instructions (Qwen, 5 licences at 560 px, test server):** 23/25 against 19/25 with the instructions first, which is what the app sends; same speed; not changed in the app yet. Image preparation (MiniCPM-V resize, Qwen document resize, PAN field canvas) was moved into `ImagePreparation` and the files from the new build are byte-identical to the old build's for the same cards.
- *Server off, seen on a phone 2026-10-26:* after Run 57 (server mode) the switch was turned off and Run 58 ran one-shot; afterwards no `libllama-server` or `libllama-mtmd-cli` process was left, so the warm server was stopped and the one-shot model unloaded. One-shot also showed the live stats and the next run number (58).
- *Not done:* 700 px (smoke-tested on Aadhaar only and not adopted); the Run details card now says "Mode: one-shot (model loaded for this scan)" for one-shot scans (built 2026-10-26, not yet seen on screen).

## 6. Open items

Needs the team lead:

1. Approve or change the nine ADRs (all Proposed).
2. Min SDK 28 against the standard's 26 (the native libraries are built for android-28).
3. Whether the on-device model stays, against the standards' direction for model calls through Python.
4. Whether the Compose, Hilt and Room stack is in scope.
5. Run-log content: it holds extracted fields on purpose (ADR 0002 row 4).
6. Approve or change option B (now in the code, ADR 0007), or choose A or C in section 5. B still needs an in-app phone check and the accuracy re-checked on more cards; C only as a fallback.

Needs the team or more work:

7. Model registry gaps: the 4B model has no file name, versions are `unversioned`, 2 checksums are missing.
8. Evaluation set and CI (ML-11, ML-12, IDP-03). Nothing runs automatically.
9. No timeout on the one-shot CLI path (LLM-05).
10. `MainActivity.kt` is still 1,322 lines (limit 400) and is excluded from ktlint and detekt. Splitting out the process start, HTTP calls and extraction flow is held until the phone checks can prove the move.
11. The native libraries are not 16 KB page-aligned (an Android 16 and Play compatibility warning). The usual remedy, a flexible-page-size build flag or a newer NDK, is untested.
12. Signing for release (the standards do not describe Android signing).

The counts in [STANDARDS_COMPLIANCE.md](STANDARDS_COMPLIANCE.md) and [TL_BRIEF.md](TL_BRIEF.md) were updated on 2026-10-26 to the current numbers: 253 tests, `MainActivity.kt` 1,322 lines.

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

## Cleanup after the merge (2026-10-26, `feat/mobile-standards-cleanup`)

Done by the Python lead after PR #9, without changing app behaviour:

| Change | Why |
|---|---|
| ADRs moved from `docs/adr/` to `mobile/idp-android/docs/adr/`; every link updated | Each part of the repo keeps its own ADRs (extraction-service has its own 0001-0003), so numbers don't clash |
| Root `CLAUDE.md` keeps the repo-wide rules; the branch rules moved to `mobile/idp-android/CLAUDE.md` | They applied to the whole repo once merged ("don't touch `services/ocr`", "merge to `dev`") |
| Card values removed from `AI_CONTEXT.md` and this file; real DL serial numbers in `DlRulesTest.kt` and `ValidationReportsTest.kt` replaced with made-up ones (state, office and year kept) | No personal data in git |
| Licence notes added to `assets/models.yaml` as comments | The registry had none. A real `licence` field needs a `ModelRegistry.kt` change plus tests, run with `scripts/check.sh` |

**Not run here:** `scripts/check.sh` (no Android SDK on the lead's machine). The test edits change only digits inside string values used consistently within each test, and the YAML change is comment lines, which the parser skips. **Run `scripts/check.sh` once on the Mac to confirm.**
