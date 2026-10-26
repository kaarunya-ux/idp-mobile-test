# mobile/idp-android: rules for AI assistants

These add to the repository's root `CLAUDE.md`. New session? Read `docs/mobile-extraction/AI_CONTEXT.md` first (status, open problems, how to build and test on the phone, traps).

## Scope
Standardizing the existing mobile document extraction only: `mobile/idp-android/`, `docs/mobile-extraction/`, `mobile/idp-android/docs/adr/`, and `research/` as read-only context (ask before changing anything there). No new features and no backend work. Do not touch `services/conversion-service/`, `services/conversions/`, `services/extraction-service/` or `mobile/idox-mobile/` (other teammates' work). PaddleOCR-VL support was removed from the app at the owner's request (2026-10-22); the findings docs and logs that mention it are history and stay. The Trinidad & Tobago validators stay put.

## Working rules
- Do not commit, push, open PRs or merge until the owner says so. Leave changes in the working tree. PRs go into `feat/idp-consolidation`, never `dev` or `main`.
- The pure parts of the model call (launch arguments, environment, request and reply JSON, output parsing) are extracted into `LlamaLaunch`, `LlamaRequest` and `OutputParser`. The process start, the HTTP calls and the extraction flow (`runOnePass`, `ensureServerRunning`, `runOnePassServer`, `runExtraction`) stay in `MainActivity.kt` until the phone check can prove a move there; unit tests cannot. RAM-tier model selection is front-end work and out of scope. Open decisions: `docs/mobile-extraction/TL_BRIEF.md`.
- Move code without changing behavior or any measured value. Keep the measurement comments with their constants.
- Before calling a step done run `mobile/idp-android/scripts/check.sh` (ktlint, detekt, 226+ unit tests, Android Lint, debug and release builds, no forbidden files tracked). Diff `MainActivity.kt` against a backup to prove only the intended lines changed. Plant a bug in a scratch copy to prove new tests can fail.
- Report in every reply: the rule IDs touched, the files changed, and any deviation. Keep `docs/mobile-extraction/STANDARDS_COMPLIANCE.md` current.
- Never silently: restructure packages, add a dependency or tool, change a threshold, prompt, output field or error code, skip a test, or add a lint suppression without a code and a reason.
- The `[ML]` rules in the Python Standard (Ch. 18) apply to the Kotlin code that loads or calls a model, even though the style rules are Python-specific. The lint limits for Kotlin are borrowed from the Python Standard (ADR 0003).
- Native libraries, model files, APKs and signing keys never go in git (`.gitignore`, and `check.sh` fails if one is tracked). The Android SDK and JDK live outside the repo under `~/Library/Android`.

## Known gaps
The existing code predates these standards. Rule-by-rule status: `docs/mobile-extraction/STANDARDS_COMPLIANCE.md`. Decisions waiting on the team lead: `docs/mobile-extraction/TL_BRIEF.md`. Deviations kept on purpose: `docs/adr/` (in this folder). `services/conversion-service/` is the reference implementation of the Python standard.
