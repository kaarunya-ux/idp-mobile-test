# IDP engineering rules for AI assistants

The full standards are in [standards_docs/](standards_docs/). Read the relevant chapter before touching code. If a rule can't be followed, stop and ask. Don't deviate silently, and report any deviation in the PR.

Precedence: Approved ADR > Python/Java Standard > team convention > library default > personal preference. Where the standards and IDP-0x specs disagree, raise it.

## Which document governs what
- Product, shared contracts, error codes, job states, limits: IDP-00
- Python services (extraction, conversion, ai-gateway): Python_Engineering_Standard + IDP-03
- Android and web apps: IDP-04, with IDP-00 Ch. 7 contracts and the IDP-02 sync/guest endpoints
- Java services (IDP-01/02, Java Standard): reference only. We are the Python team and no Java is written here. Git/PR rules (Java Standard Ch. 33) are shared with the Python Standard, so they still apply.

## Scope of this branch (`mobile_standards`)
Standardizing the existing mobile document extraction only: `mobile/idp-android/`, `docs/mobile-extraction/`, `docs/adr/`, and `research/` as read-only context (ask before changing anything there). No new features and no backend work. Do not touch `services/conversion-service/`, `services/conversions/`, `services/ocr/` or `mobile/idox-mobile/` (other teammates' work). PaddleOCR-VL support was removed from the app at the owner's request (2026-10-22); the findings docs and logs that mention it are history and stay. The Trinidad & Tobago validators stay put.

## Working rules for this branch
- Do not commit, push, open PRs or merge until the owner says so. Leave changes in the working tree. Merging to `dev` happens once, at the end.
- The pure parts of the model call (launch arguments, environment, request and reply JSON, output parsing) are extracted into `LlamaLaunch`, `LlamaRequest` and `OutputParser`. The process start, the HTTP calls and the extraction flow (`runOnePass`, `ensureServerRunning`, `runOnePassServer`, `runExtraction`) stay in `MainActivity.kt` until the phone check can prove a move there; unit tests cannot. RAM-tier model selection is front-end work and out of scope. Open decisions: `docs/mobile-extraction/TL_BRIEF.md`.
- Move code without changing behavior or any measured value. Keep the measurement comments with their constants.
- Before calling a step done run `mobile/idp-android/scripts/check.sh` (ktlint, detekt, 226+ unit tests, Android Lint, debug and release builds, no forbidden files tracked). Diff `MainActivity.kt` against a backup to prove only the intended lines changed. Plant a bug in a scratch copy to prove new tests can fail.
- Report in every reply: the rule IDs touched, the files changed, and any deviation. Keep `docs/mobile-extraction/STANDARDS_COMPLIANCE.md` current.
- Never silently: restructure packages, add a dependency or tool, change a threshold, prompt, output field or error code, skip a test, or add a lint suppression without a code and a reason.
- The `[ML]` rules in the Python Standard (Ch. 18) apply to the Kotlin code that loads or calls a model, even though the style rules are Python-specific. The lint limits for Kotlin are borrowed from the Python Standard (ADR 0003).
- Native libraries, model files, APKs and signing keys never go in git (`.gitignore`, and `check.sh` fails if one is tracked). The Android SDK and JDK live outside the repo under `~/Library/Android`.

## Python (services/)
- Python 3.12, `uv` only (`pyproject.toml` + `uv.lock`). No `requirements.txt`, no pip, no `requests`.
- Feature-first: `<feature>/{api,application,domain,infrastructure}`. No `utils.py`/`helpers.py`/`common.py`. See Python Standard Ch. 3.3 for where each file goes.
- Domain is pure: no framework or I/O imports, no `datetime.now()`/`uuid4()`; inject the clock. Request/Response/Message/Dto/Orm objects never cross layers.
- Full type hints (`mypy --strict`), `ruff` (line length 120), no `print`, no bare `except`, no `# type: ignore`/`# noqa` without a code and a reason. Functions ≤ 50 lines, complexity ≤ 10, ≤ 7 params, modules ≤ 400 lines.
- Config only through the pydantic-settings `Settings` class. No `os.environ` elsewhere. Thresholds live in settings, never as literals (ML-07).
- Never block the event loop. CPU work (OCR, PDF, image) goes to a worker. Never call a service from another service class.
- Tests: pytest, `test_<expected>_when_<condition>`, import-linter contracts, coverage ≥ 80% on new code. `make check` must be green before "done".
- Never silently: restructure packages, add a dependency, change an error code or API field, skip a test.

## Android (mobile/)
Target architecture (IDP-04 Ch. 3): Kotlin 2.x, Compose + Material 3, min SDK 26 / target 35, MVVM with layers `ui → domain → data`, Hilt, Coroutines/Flow, Retrofit client generated from OpenAPI, Room + SQLCipher (key in Keystore), WorkManager, AppAuth (Keycloak PKCE), ML Kit for capture/OCR, Coil.
- Modules: `:app`, `:core:*`, `:engine:*`, `:feature:*`. Names: `<Name>Screen`, `<Name>ViewModel`, `<Name>UiState`, `<Name>Event`, `<Noun>Repository`/`Offline<Noun>Repository`, `<Verb><Noun>UseCase`.
- Room is the single source of truth for the UI. Tokens and the DB key sit behind the Android Keystore. No document content in logs or crash reports. HTTPS only via network security config. Thresholds come from the template bundle, not literals.
- Only talk to the API gateway and MinIO presigned URLs. Contract and error codes come from `idp-contracts`.
- Tests: view models, repositories, rules (golden cases), quality gate, Room migrations, WorkManager, Compose UI.

## Git / PRs (Python Standard Ch. 25, Java Standard Ch. 33)
- The standards ask for `feat/<TICKET>-<slug>` branches, Conventional Commits, PRs of about 400 changed lines with what/why/tests/risk, and an ADR for deviations and new dependencies. For this branch there are no PRs until the end, and nothing is committed or pushed until the owner says so (see "Working rules for this branch").

## Known gaps
The existing code predates these standards. Rule-by-rule status: `docs/mobile-extraction/STANDARDS_COMPLIANCE.md`. Decisions waiting on the team lead: `docs/mobile-extraction/TL_BRIEF.md`. Deviations kept on purpose: `docs/adr/`. `services/conversion-service/` is the reference implementation of the Python standard.
