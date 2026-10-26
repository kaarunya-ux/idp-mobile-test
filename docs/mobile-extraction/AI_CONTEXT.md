# Context for an AI assistant picking up the mobile extraction work

Written 2026-10-26 for another AI coding assistant (or a new engineer) that starts a session on this branch with no memory of the earlier ones. Read this first, then `/CLAUDE.md` (the rules), then the documents named under "Where things are written down". Everything here was checked on 2026-10-26; where something is a guess it says so.

## 1. What this project is

An Android app (`mobile/idp-android/`, Kotlin, package `com.modeltesting.vlmtester`) that reads Indian identity documents (Aadhaar, driving licence, passport, PAN) **on the phone, offline**, with a small vision-language model running through llama.cpp on the CPU. The user picks a photo, the app shrinks it, sends it with a short prompt to a local model process, checks the JSON answer with rules, and shows the fields. The Trinidad and Tobago validators are kept but have no real test cards.

Branch `mobile_standards`. The job on this branch is to **standardize the existing mobile extraction to the IDP standards** (`standards_docs/`, git-ignored) and to make it fast and accurate enough to demonstrate. No new product features, no backend work, and do **not touch** `services/conversion-service/`, `services/conversions/`, `services/ocr/`, `mobile/idox-mobile/` (other people's work). `research/` is read-only.

Hard rules from the owner (also in `/CLAUDE.md`):
- **Do not commit, push, open PRs or merge until the owner says so.** As of this writing about 25 files are changed or new and uncommitted (the last commits are `d3a8721` and `5cb6f57`, made by the owner).
- Native libraries (`jniLibs/`), model files, APKs and signing keys never go in git. `data/` and `standards_docs/` are git-ignored.
- Move code without changing behaviour or any measured value. Never silently change a threshold, prompt, output field or error code, add a dependency, or add a lint suppression without a code and a reason.
- Before calling anything done run `mobile/idp-android/scripts/check.sh` (ktlint, detekt, unit tests, Android Lint, debug and release builds, forbidden-files check, coverage floor). Diff `MainActivity.kt` against a backup to prove only the intended lines changed. Plant a bug to prove a new test can fail.
- Report in every reply: rule IDs touched, files changed, deviations.
- Explain things plainly. The owner asked repeatedly for short, simple explanations and for no large changes without saying what they are for.

## 2. The three models and what they are good at

Phone: Samsung Galaxy M35 (SM-M356B, Exynos 1380, 5.5 GB RAM, CPU only; cores 4-7 are the fast ones, the app pins to them). Models live in `/sdcard/Android/data/com.modeltesting.vlmtester/files/models`. The 3B model is meant for 8 GB phones; the 5.5 GB phone was used on purpose as a stress test.

| Model (file) | Good at | Weak at | Typical scan time (560 px) |
|---|---|---|---|
| Qwen3-VL 2B (`qwen3vl-2b-instruct-q4_k_m.gguf`) | licences and passports on clean cards, PAN (480 px profile, 5/5) | dates and digits at small sizes; invents an address on front-only Aadhaar; its own Aadhaar profile is slow (long prompt and address) | licence about 20 s, passport 26-32 s, PAN 14-22 s, Aadhaar 39-47 s |
| MiniCPM-V 4.6 (`minicpm-v-4.6-q4_k_m.gguf`) | Aadhaar (12/12 so far), PAN with the field canvas | passports (names garbled), one case where server mode and one-shot mode gave different answers for the same image | Aadhaar about 20 s, passport 27-39 s |
| LFM2-VL 3B (`LFM2-VL-3B-Q4_K_M.gguf`) | passports (Attarwala 7/7) | reads the **father's name as the holder's** (Rathore card, every run); needs about 2.3 GB resident, so memory stalls on 5.5 GB | passport 33-50 s, Aadhaar 31-40 s |
| Qwen3-VL 4B | **never tested** (needs the second phone) | | |

All accuracy numbers are hand-scored on 7 to 10 clean cards per model, one run each. Treat them as indicative. Per-run detail: `ON_DEVICE_LATENCY_AND_ACCURACY_FINDINGS.md` section 3a and `STANDARDIZATION_HANDOFF.md` ("In-app results", "Later in-app results").

## 3. What was found and changed (the story in order)

1. **Standards work (earlier sessions).** The pure logic (rules, parsing, request building, launch arguments, image-size arithmetic) was moved out of the 2,660-line `MainActivity.kt` into tested files; lint (ktlint, detekt), JaCoCo, gitleaks, prompt versioning with pinned hashes, a model registry, ADRs 0001-0006. Details: `STANDARDS_COMPLIANCE.md`.
2. **LFM2 was slow (60-100 s). Root cause found.** The llama.cpp build in the app (commit `000bee5`, upstream PR #27057) makes LFM2's image preprocessor **split a large image into tiles** when the image holds more than about 262,000 pixels at our token cap of 128; a PAN card became 15 tiles, about 3,800 image tokens. Fix: shrink the image before sending (LFM2: PAN 500 px, other documents 560 px) — ADR 0007. Qwen and MiniCPM are not affected by the tiling.
3. **Prompts were long.** DL, passport and Aadhaar used about 800-token prompts and asked for every field. A compact prompt plus a small GBNF grammar (5 fields for DL, 7 for passport, 4 for Aadhaar) cut scans 2-3x — ADR 0008. **Fewer fields come back (no address, blood group, vehicle classes); this is a product decision still waiting on the team lead.** Qwen keeps its own Aadhaar profile (asks for the address, 38-47 s).
4. **Qwen images were sent at full size** (60-75 s). Now capped at **560 px longest edge for DL, passport and Aadhaar** — ADR 0009. Accuracy cost about 4-5 fields per set. The owner chose to keep 560 px after a smoke test showed 700 px read Aadhaar addresses better for 6-10 s more. 400 px was tried and rejected.
5. **Rules added** after wrong values scored HIGH: real calendar dates, licence number starting with `DL NO`, licence issue year from 1950, **passport expiry must be after issue**. HIGH still only means "all fields were filled", not "correct" (open item).
6. **Memory.** One server model plus a one-shot scan used two copies; fixed (turning server mode off stops the server — seen working on the phone). LFM2 on 5.5 GB shows memory stalls of 20-50%; Qwen does not.
7. **UI.** After a scan the output panel shows only `Label: value` lines plus the check verdict (`ResultDisplay`); processor, mode, model, threads, image-token limit, estimated image tokens, image size, server warm/cold, tokens and read/write/total time are in a separate "Run details" card under the live stats (`RunInfoDisplay`). The full text is still saved to `run_log.txt` unchanged.
8. **`MainActivity.kt` split, stages 1-3 done** (2,660 → 1,322 lines): `TtRules`, `ValidationReports` (proved by 6,000 random comparisons against the old code), `ProcessMonitor` and `RunCounter`, `ImagePreparation` (resized files byte-identical to the old build's on the phone). **The owner decided to stop the split here.** Stages 4-7 (`CliScan`, `ModelServer`, `ExtractionFlow`, long-function breakup, lint on `MainActivity.kt`) are listed in `TL_BRIEF.md` as "before release" work.

## 4. Open problems, in the order I would take them

1. MiniCPM-V gave different answers for the **same image** in server mode (Run 63, 5/7) and one-shot mode (Run 66, 7/7). The two paths send different settings (the server request in `LlamaRequest.kt` vs the CLI flags in `runOnePass`, for example repeat penalty, chat template options, prompt order). Not investigated.
2. **Qwen sees the instructions first, then the image** (`imageFirst = isLfm2` in `MainActivity.kt`). On 5 licence cards at 560 px (test server): image first 23/25 fields, instructions first (the app today) 19/25, same speed. Passport and Aadhaar not measured image-first. Qwen's PAN profile was validated instructions-first and should stay as it is.
3. LFM2 passport prompt: make it say the holder's own name beside Given Name(s) and Surname, not the Name of Father line. Needs a new prompt version (LLM-02) and a test on all passport cards.
4. Stronger checks: licence validity after issue date, other cross-field rules (a wrong Kerala licence in Run 38 scored HIGH).
5. Front-only Aadhaar: Qwen invents an address and PIN (the check caught one, Run 27).
6. An evaluation set of real camera photos with confirmed values and automatic scoring (ML-11, ML-12). All current images are clean saved images.
7. Ideas written up but not built: choose the image size per scan (OCR confidence first, retry bigger when the check fails), best model per document, the 4B test. See `TL_BRIEF.md` "Future work and ideas".

## 5. Where things are

Code (`mobile/idp-android/app/src/main/java/com/modeltesting/vlmtester/`, module map in the `README.md` next to it):
- `MainActivity.kt` — the screen, the scan flow (`runExtraction`), the server handling (`ensureServerRunning`, `runOnePassServer`, `stopServer`, warm-up), the one-shot scan (`runOnePass`). **Excluded from ktlint, detekt, unit tests and coverage** until split. Still over the 400-line limit.
- `ModelProfiles.kt` (measured constants with their evidence, which profile applies), `ExtractionPlan.kt` (one place that decides prompt, grammar, image preparation, size, compact or not, per document and model), `ImageSizing.kt`, `ImagePreparation.kt` (Bitmap resize and PAN canvas; no unit tests).
- Rules: `ExtractionRules`, `PanRules`, `AadhaarRules`, `DlRules`, `PassportRules`, `Stage1Rules`, `TtRules`, `ValidationReports` (which check runs for which document type), `OutputParser`.
- Model call: `LlamaLaunch` (arguments and environment), `LlamaRequest` (server JSON), `ProcessMonitor` (live stats, process ids), `RunLog` and `RunCounter`, `ResultDisplay`, `RunInfoDisplay`.
- Assets: `assets/prompts/*.md` (versioned, SHA-256 pinned in `PromptFileTest`), `assets/grammars/*.gbnf`, `assets/models.yaml` (model registry).
- Tests: `app/src/test/java/...` — about 278 JVM tests; helper `json(...)` in `TestJson.kt`.

Documents (`docs/mobile-extraction/`, `docs/adr/`):
- `TL_BRIEF.md` — decisions needed from the team lead plus the future-work list. **Start here for status.**
- `STANDARDIZATION_HANDOFF.md` — what changed, measured results, how to rebuild and test.
- `STANDARDS_COMPLIANCE.md` — rule-by-rule status (Met / Partly / Not met).
- `ON_DEVICE_LATENCY_AND_ACCURACY_FINDINGS.md` — the speed and accuracy measurements.
- ADRs 0001-0009 (all "Proposed", none approved yet). Older material (`HANDOVER*.md`, `*_FINDINGS.md`, PDFs) is history from before the standards work; do not rewrite it.

Test data (not in git): `data/india_docs/{aadhaar,dl,passport,pan}/` with images and `truth.md` (values read from the images by an AI on 2026-10-23, **owner still to confirm**; PAN has no truth file, `card_a` = OMRPS9699G / ROSHAN SAHANI / RAMESH SAHANI / 27/02/2002, `card_b` = QFVPS0764H / SAMAD / MOHD SAJID / 03/02/2004). On the phone: `/sdcard/Download/idp_india_docs/` (dl_1-5, pp_1-5, aa_1-6) and `/sdcard/Download/pan_tests/` (card_a..e_front.jpg).

## 6. How to build, test and look at results

```
cd mobile/idp-android
export JAVA_HOME=~/Library/Android/jdk-17            # JDK 17 lives outside the repo
export PATH=$PATH:~/Library/Android/sdk/platform-tools   # adb
scripts/check.sh                                     # the gate; prints "ALL CHECKS PASSED"
./gradlew -q installDebug                            # install on the connected phone
./gradlew --stop                                     # frees about 1 GB of RAM afterwards
adb shell run-as com.modeltesting.vlmtester cat files/run_log.txt   # every scan: header, answer, check (debug build only)
adb logcat -d -s LlamaServer                         # server timings
```
- The phone serial was `RZCXA1SXH5V`. Native libraries (`jniLibs/arm64-v8a`, git-ignored) are built by `native/build_llama_dotprod.sh` (pinned llama.cpp `000bee5`); if the folder is empty, ask the owner.
- The app shows a "not 16 KB-compatible" dialog at launch (known open item); tap OK (about x=246, y=2170 on the 1080x2340 screen) or it blocks the screen.
- "Persistent server mode" is **not remembered**: it resets to off whenever the app restarts or is reinstalled. Server-mode scans say `server mode` in the log header and report prompt/read/write times; one-shot scans do not ("perf: not reported by binary").
- To free the phone: `adb shell run-as com.modeltesting.vlmtester kill -9 <pid>` (plain `adb shell kill` is refused because the model runs as the app's user). `pkill -f ...` over adb can match its own shell.
- Scoring: compare the answer in `run_log.txt` with `truth.md` field by field; the app's own HIGH/MEDIUM/LOW label is not a correctness measure.

## 7. Things that cost time before (so you do not repeat them)

- Image tokens are about 1 per 32x32 pixels; the image-token **cap is a server start-up setting** (`LLAMA_ARG_IMAGE_MAX_TOKENS`), a ceiling not a target. The lever per scan is the resize edge. Prefill is about 10-25 tokens per second, writing 8-13 tokens per second on this phone.
- Test-server experiments (llama-server on the phone via adb, curl through `adb forward`) must use the app's exact flags and the app's request shape. **A test that differs in image-before-text order or other settings can give different answers** (this caught out the Qwen sweep). Never kill a request early (the single slot stays busy about 40 s), wait for the server to be idle between requests, and keep the phone cool. Scratch scripts live outside the repo and are not kept.
- macOS: `sed -i ''`, no `timeout` command, zsh does not word-split unquoted variables. ktlint wants imports in plain ASCII order with `java.*` before `org.*`, single-line function signatures when they fit in 120 characters, no multi-line boolean conditions with operators mid-line; detekt flags swallowed exceptions and complexity over 10 (use `runCatching` or split the function).
- Do not trust a summary of earlier measurements without checking the run log: several early beliefs were wrong (for example that LFM2 PAN was already shrunk, or that DL needed only the resize).
- The Kerala licence was scored 3/5 for a while by mistake; the correct score is 2/5 (the licence prefix `KL` was read as `DL`).

## 8. What "done" looks like for the next change

1. Change code or prompts in small steps; keep measurement comments with their constants.
2. `scripts/check.sh` passes; add unit tests for pure code; plant a bug to prove they fail.
3. Anything that touches the model call, image handling or scan flow is proved on the phone (scan each model once, compare the log) because unit tests cannot.
4. Update `STANDARDS_COMPLIANCE.md`, `STANDARDIZATION_HANDOFF.md` and, if it is a decision or a known risk, `TL_BRIEF.md`. A new default, threshold or prompt needs an ADR (Proposed).
5. Do not commit or push unless the owner says so.
