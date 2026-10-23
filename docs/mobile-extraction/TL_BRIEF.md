# Mobile extraction: decisions needed from the team lead

One page. Status of the standardization of `mobile/idp-android` (the on-device Aadhaar/PAN extraction app) and the decisions only the team lead can make. Details: `STANDARDS_COMPLIANCE.md`, `docs/adr/`.

## Where we are
- The existing app was tidied to the IDP standards without adding features and without touching the servers or other teams' folders.
- 19 pure, tested code files were separated from the 2,660-line `MainActivity.kt` (now 1,781 lines): the Aadhaar, PAN, Indian DL and Indian passport checks, the model profiles and resize rules, the run-log and file rules, the output parser, the process-stats parser, and the llama.cpp launch arguments and request/reply JSON. PaddleOCR-VL support was removed from the app (not needed).
- 231 unit tests pass; lint (ktlint, detekt), Android Lint (0 errors) and both builds (debug, minified release) pass. One command runs it all: `mobile/idp-android/scripts/check.sh`.
- Security fixes: cleartext limited to the app's own local server, run logs kept in private storage.
- The app has run on a Galaxy M35 (Android 16): install, model loading, scans, the new run-log header and the disabled GPU switch work. The 21 checks in `PHONE_CHECKLIST.md` are not run systematically yet. Nothing is pushed or merged.
- One performance finding needs a decision (item 8 below): LFM2 scans take about 90 s with the libraries the pinned build script produces, against 25 to 36 s with the old libraries. Full evidence: `STANDARDIZATION_HANDOFF.md`, section 5.

## Decisions needed

**1. Does the on-device model stay? (the one that decides the most)**
- IDP-04 Ch. 3.4 describes two paths. Online: the phone uploads and the Python `extraction-service` extracts (line 265). Offline: ML Kit OCR, template rules, then the on-device SLM "only for fields that fail rules" (line 267). F-EX-15 (offline extraction, Phase 4) and open decision D-2 (on-device SLM and size per RAM tier) cover it.
- Our app is the on-device part only. Keep it, replace it, or drop it?
- The pure parts of the model call are already extracted and tested. The process start, HTTP calls and extraction flow stay in `MainActivity.kt` until a phone check can prove a move there. A decision to drop the on-device model would make the rest of that restructuring unnecessary.

**2. RAM tiers (D-2): dropped from this work.** Choosing the model per RAM tier is front-end (mobile app) work, not part of this standardization. Today the model is picked by hand and stays that way.

**3. Compose, Hilt and Room.** IDP-04 Ch. 3.1 expects Jetpack Compose (screens), Hilt (wiring) and an encrypted Room database. The app uses old-style screens and plain files. Moving to them is a rewrite of the UI and storage, not a cleanup. Do we leave it for now (our recommendation; ADR 0002 row 3) or plan it? It affects only the Android app, not the Python services.

**4. Release and signing.** Google Play with Play App Signing (the standard delivers the model by Play Asset Delivery) or another route? Who holds the upload key? The release build is minified (30.7 MB) and unsigned.

**5. The run log.** It keeps extracted fields as plain text in private storage, which is useful for testing. For a release build: keep, remove, or encrypt? (OBS-04, LLM-06.)

**6. Approval of six ADRs** (all "Proposed"): 0001 keep llama.cpp for now; 0002 the deviations we are knowingly keeping; 0003 ktlint and detekt, with limits borrowed from the Python Standard; 0004 PaddleOCR-VL removed from the app and the test-only dependencies added; 0006 coverage measurement (JaCoCo) and a secret scan (gitleaks); 0005 prompt files versioned (`<name>.v1.md` with front matter) and a model registry added, as the Python Standard prescribes.

**7. Git.** When do we commit, push and merge (nothing is committed since `834025f`, nothing pushed)? Should `standards_docs/` and `CLAUDE.md` go into the repo? Naming: ticket ids and `feat/<TICKET>` branches are not used.

**8. LFM2 speed with the pinned llama.cpp build.** The new `libmtmd.so` (llama.cpp `000bee5`, upstream PR #27057) splits a PAN card into 15 tiles at our 128-token cap, about 3,800 image tokens instead of 112, so LFM2 `pan_full` takes 85 to 95+ s instead of 16 to 36 s. Qwen3-VL and MiniCPM-V are not affected. Options: **A** pin an older upstream commit (not tested); **B** shrink the LFM2 input in Kotlin (PAN 500 px, Aadhaar, DL and passport 560 px; command-line tests: 14 to 29 s; PAN 24/25 fields correct on five cards). **B is now in the working tree** (ADR 0007, 231 tests pass, nothing committed) and waits for your approval and an in-app phone check; **C** keep the old `libmtmd.so`, which has no documented source and cannot be rebuilt. Our recommendation: B, with accuracy re-checked on more cards and in the app. A further finding: LFM2 needs 2.2 to 2.7 GB of free RAM and stalls on a 5.5 GB phone when memory runs short (`ON_DEVICE_LATENCY_AND_ACCURACY_FINDINGS.md`). Detail and tables: `STANDARDIZATION_HANDOFF.md`, section 5.

## Not decisions, but needed from the team
- A phone, the 2B/4B model files and ground-truth cards for one end-to-end check (`PHONE_CHECKLIST.md`).
- Who built the old native libraries (build folder `/home/kaarunya/toolchain/build_check2`) and whether they were patched; the build script has never been run end to end here.
- The server-side pieces the phone would use later (the `RECHECK` job and the template bundle, IDP-03 lines 142 and 203) belong to the Python `extraction-service`, which does not exist in the repo yet. They are outside this branch.
