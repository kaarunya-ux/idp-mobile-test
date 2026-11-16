# IDP engineering rules for AI assistants

The full standards are in [standards_docs/](standards_docs/). Read the relevant chapter before touching code. If a rule can't be followed, stop and ask. Don't deviate silently, and report any deviation in the PR.

Precedence: Approved ADR > Python/Java Standard > team convention > library default > personal preference. Where the standards and IDP-0x specs disagree, raise it.

## Which document governs what
- Product, shared contracts, error codes, job states, limits: IDP-00
- Python services (extraction, conversion, ai-gateway): Python_Engineering_Standard + IDP-03
- Android and web apps: IDP-04, with IDP-00 Ch. 7 contracts and the IDP-02 sync/guest endpoints
- Java services (IDP-01/02, Java Standard): reference only. We are the Python team and no Java is written here. Git/PR rules (Java Standard Ch. 33) are shared with the Python Standard, so they still apply.

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
- Branches `feat/<slug>` (or `feat/<TICKET>-<slug>`), Conventional Commits, PRs of about 400 changed lines with what/why/tests/risk, and an ADR for deviations and new dependencies.
- PRs go into `feat/idp-consolidation`. `dev` and `main` are not used by this team.
- The owner reviews and merges. An AI assistant does not commit, push, open PRs or merge unless the owner says so.
- No personal data in git: card images, real ID numbers and names stay local (`.gitignore`); tests use made-up values.

## Where each part keeps its notes
- `services/conversion-service/`: the reference implementation of the Python standard. Build log: `docs/conversion-service/build-log.md`.
- `services/extraction-service/`: ADRs in its `docs/adr/`. Build log: `docs/extraction-service/build-log.md`.
- `mobile/idp-android/`: its own `CLAUDE.md` (rules for that app), ADRs in its `docs/adr/`, and status in `docs/mobile-extraction/` (start with `AI_CONTEXT.md`).
