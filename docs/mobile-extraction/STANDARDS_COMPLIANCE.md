# Standards compliance: mobile extraction (`mobile/idp-android`)

Where the app stands against the IDP standards, rule by rule. Status updated 2026-10-26 (first written 2026-10-22), on the `mobile_standards` branch, uncommitted.

**How to read it.** *Met* has evidence you can run. *Partly* says what is missing. *Not met* has a plan or an ADR. *Held* waits on a decision from the team lead (the on-device model direction). *N/A today* means the rule is about something this app does not have yet (accounts, a database, a server connection). Nothing here has run on a phone; "Met" means built, linted and unit-tested on a Mac.

Evidence command: `mobile/idp-android/scripts/check.sh` (278 unit tests, ktlint, detekt, Android Lint, debug and release builds, no forbidden files tracked).

Sources: IDP-04 (mobile), the Python Engineering Standard (the `[ML]` rules apply to any code that calls a model; the style and size limits are applied to Kotlin by analogy, see ADR 0003), the Java Standard Ch. 33–34 (git, ADRs, documentation, which the Python Standard adopts).

## 1. Structure

| Rule | Asks for | Status | Evidence / plan |
|---|---|---|---|
| IDP-04 3.2 | Modules `:app`, `:core:*`, `:engine:*`, `:feature:*` | Not met | One `:app` module. The pure code is in separate files already (`engine`-type code). ADR 0002 row 3 |
| IDP-04 3.2 | Naming: `*Screen`, `*ViewModel`, `*UiState`, `*Repository`, `*UseCase` | Not met | The UI is one activity. Waits on the stack decision |
| IDP-04 3.1 | Compose, Hilt, Room + SQLCipher, WorkManager, Retrofit, AppAuth | Not met | ADR 0002 row 3. Whether it is in scope is a question for the team lead |
| IDP-04 3.1 | Kotlin 2.x | Not met | Kotlin 1.9.24 |
| IDP-04 3.1 | min SDK 26 | Not met | 28, because the native libraries are built for android-28. ADR 0002 row 2 |
| PKG-01 | No `utils` / `helpers` / `common` files | Met | None exist |
| PY-11 / Ch. 27.2 | Files ≤ 400 lines | Partly | All 26 extracted files are under 400 (ResultDisplay, RunInfoDisplay, TtRules, ValidationReports, ProcessMonitor and ImagePreparation were added 2026-10-26; the Trinidad and Tobago checks and the choice of check per document type moved out of `MainActivity.kt`, proved by 6,000 random comparisons against the old code and planted-bug checks; the live-stats monitor and the image resizing moved out too, proved on the phone, with the resized files byte-identical to the old build's. `ImagePreparation` needs Android's Bitmap classes, so unit tests cannot cover it). `MainActivity.kt` is 1,322 |
| PY-11 / Ch. 27.2 | Functions ≤ 50 lines, complexity ≤ 10, ≤ 7 parameters | Partly | Enforced by detekt on the 19 extracted files. `MainActivity.kt` is not checked yet |
| DOM-01 | Domain code has no framework or I/O imports | Met | The `*Rules`, `ModelProfiles`, `ImageSizing`, `ModelFiles`, `ProcessStats`, `OutputParser` files import only `org.json` (the platform JSON) and the Kotlin library |
| DOM-05 | Time and randomness injected, never read inside the domain | Met | The calendar year is a parameter in every rule; `RunLog` takes the date as an argument |
| NAM-02 | No `handle` / `process` / `run` / `execute` / `manage` names | Partly | New code complies. `MainActivity.kt` has `runExtraction`, `runOnePass` (held with the server and flow splits) |

## 2. Security (IDP-04 3.8)

| Rule | Asks for | Status | Evidence / plan |
|---|---|---|---|
| 3.8 | HTTPS only | Partly | Cleartext is limited to `127.0.0.1` and `localhost` by `network_security_config.xml`. ADR 0002 row 1 |
| 3.8 / OBS-04 | No document content in logs | Partly | The server's logcat output is debug-only. The run log (private storage) holds extracted fields on purpose. ADR 0002 row 4. Whether this is acceptable is a question for the team lead |
| 3.8 | Keystore for tokens and the database key | N/A today | The app has no tokens and no database |
| 3.8 | `FLAG_SECURE` on result screens when the organisation policy masks fields | N/A today | There are no accounts or policies |
| 3.8 | Play Integrity on guest sessions and purchases; sign-out wipes the database | N/A today | No server connection |
| 3.8 | Nothing leaves the phone through backup or device transfer | Met | `allowBackup="false"` and `data_extraction_rules.xml` exclude the app's files, database, preferences and external files from cloud backup and device-to-device transfer. Not yet seen on a phone |
| CFG-03 | No secrets in git | Met | `*.jks`, `*.keystore`, `local.properties`, `.env` ignored; the check script fails if any is tracked |
| Release | Signed release build | Not met | The release build is minified and unsigned. The standards do not describe Android signing or distribution, so no signing is added |
| BLD-02, DPL-01/02 | Pinned, reproducible builds; never "latest" | Partly | `native/build_llama_dotprod.sh` is pinned to llama.cpp commit 000bee5 (found inside the shipped libraries), NDK and CMake versions fixed, no machine-specific paths. Tested with stand-in tools only; a real compile has not been run here. **Found 2026-10-22:** the "dist_dot" libraries built on another machine from this commit tile LFM2 images and make LFM2 about 3x slower than the old libraries (handoff section 5); until the team lead picks a fix the build is not reproducible to the old speed. The app now shrinks LFM2 input images (ADR 0007, option B), which restores LFM2 speed in command-line tests; not yet run in the app. The Gradle dependencies use fixed versions |

## 3. Model calling (Python Standard Ch. 18, `[ML]`)

| Rule | Asks for | Status | Evidence / plan |
|---|---|---|---|
| LLM-01 | Call models only through an adapter, not from business code | Partly | The launch arguments, environment, request and reply JSON and output parsing are in `LlamaLaunch`, `LlamaRequest`, `OutputParser` (pure, 50 tests). The process start and HTTP calls are still in `MainActivity.kt`; moving them needs the phone check |
| LLM-02 | Prompts as versioned files, version recorded on the output | Met | All 18 prompts (15 original, 3 compact: DL, passport, Aadhaar) are `<name>.v1.md` with front matter (model, temperature, schema); the run-log header records `promptVersion`; a test pins the SHA-256 of every body. The evaluation that must accompany a new version (ML-12) is not automated. ADR 0005 |
| LLM-03 | Structured output, validated | Met | Grammar and JSON schema constrain the output; `PanRules`, `AadhaarRules`, `DlRules`, `PassportRules`, `Stage1Rules` validate it |
| LLM-04 | Document text is data; model output never triggers actions unchecked | Met | `OutputParser` rejects output copied from our own prompts; 16 tests |
| LLM-05 | Timeouts, bounded retries, concurrency limits | Partly | Audited 2026-10-22. Server path: connect 10 s, read 300 s, a 30 s readiness poll, one warm-up retry after 10 s (all bounded); Qwen and LFM2 run one slot; the Run button is disabled during a run. **Gap:** the one-shot (CLI) path has no inference timeout; it ends when the model finishes or the user presses Stop (also ML-06). Fixing it changes the process handling, which waits for the phone check |
| LLM-06 | No full prompts or document content in logs | Partly | As for OBS-04 above |
| ML-01 | Model registry (name, version, source, SHA-256) | Partly | `assets/models.yaml` exists with 5 models and a strict reader. Facts the docs do not state are `null`: no checksums, one version, the 4B has no file name. The app still picks a model by file name, not by registry name. ADR 0005 |
| ML-02 | Checksummed models, no download at request time | Partly | Nothing is downloaded at request time. Models are pushed by hand; the registry has room for the SHA-256 but the app does not check it yet |
| ML-03 | Load once per process, readiness check | Partly | In server mode the model stays loaded across runs and a health check gates readiness |
| ML-04 | Model name and version on every output | Partly | The run-log header records the model file, the registry name, version (`unversioned` until known), runtime, threads, CPU or GPU, server or one-shot, token cap and prompt version. Versions and checksums wait for the model files |
| ML-05 | Deterministic preprocessing | Partly | Audited 2026-10-22. No randomness: temperature 0 (greedy) on both paths, no sampling seed needed. Every image step uses fixed numbers: JPEG quality 92 (95 for the field canvas), a power-of-two decode step then a bilinear resize, the PDF page rendered at a fixed 2x on white, fixed card bands. **Not explicit:** the colour space is left to Android's default, and the decode, resize and JPEG encode are done by the Android platform, so identical output is guaranteed on one phone and Android version, not across them. Photos are not rotated by their EXIF orientation |
| ML-06 | Cap input size and set an inference timeout | Met | Image-edge and token caps (`ModelProfiles`, `ImageSizing`, pinned by tests); request timeouts |
| ML-07 | Thresholds in settings, changed only with an evaluation report | Partly | Collected in `ModelProfiles` with their measurement notes and pinned by tests, but still constants |
| ML-10 | Explicit CPU fallback, never silent | Met | Audited 2026-10-22. The "Use GPU (Vulkan)" switch existed although the shipped libraries have no GPU backend (CPU only), so turning it on would have silently run on the CPU while the run log said GPU. The switch is now disabled with the text "GPU (Vulkan) is not in this build - runs on the CPU only" when no Vulkan backend library is present (`GpuSupport`, 5 tests). Not yet seen on a phone |
| ML-11 / ML-12 | Labelled evaluation set, golden-set regression in CI | Not met | Benchmark results exist in `research/`; nothing runs automatically |
| IDP-04 3.4 | On-device quality gate (blur, brightness, glare, resolution) | Not met | None in the app (blur detection was tried and dropped, per a code comment) |

## 4. Tests (IDP-04 3.9, Ch. 21)

| Rule | Asks for | Status | Evidence / plan |
|---|---|---|---|
| 3.9 | Unit tests for rules | Met | 278 tests; planted-bug checks on each new group |
| 3.9 / ML-10 | Rule checks catch impossible values | Partly | Added 2026-10-26: real calendar dates, a licence number starting with `DL NO`, licence issue year from 1950, passport expiry must be after issue (`ExtractionRules`, `DlRules`, `PassportRules`, with tests). The label HIGH still means "all fields filled", not "correct": wrong values were HIGH in Runs 21, 31, 33, 34, 38, 48 and 50. TL_BRIEF future work item 2 |
| IDP-04 / OBS-04 | Result screen shows fields only; run details in their own card | Met | `ResultDisplay` and `RunInfoDisplay` (pure, 5 tests); the saved run log is unchanged. Seen on a phone 2026-10-26. No document content added to logs |
| TST-10 | No `sleep` in tests | Met | |
| 21.2, 27.2 | Coverage ≥ 85% of lines on domain code, ≥ 70% of branches | Met | JaCoCo (ADR 0006): 91.7% of lines and 86.6% of branches of the extracted logic; `check.sh` fails below the floors. `MainActivity.kt` is not measured (needs a phone) |
| 3.9 | View-model, repository, Room, WorkManager, Compose tests | N/A today | Those layers do not exist |
| 3.9 | Quality-gate tests with golden images | N/A today | No quality gate |
| 3.9 | Macrobenchmark for startup and scrolling | Not met | |
| 3.9 | Server-side shared golden cases | N/A today | The Python rules service does not exist yet |

## 5. Quality gates (Ch. 27)

| Rule | Asks for | Status | Evidence / plan |
|---|---|---|---|
| 27.1 gate 1 | Format and lint | Partly | ktlint and detekt pass on the extracted files; `MainActivity.kt` is switched off until it is split. ADR 0003 |
| 27.1 gate 2 | Types and architecture checks | Met / Not met | The compiler checks types. There is no architecture-rule tool for the layers |
| 27.1 gate 4 | Dependency, secret and image scans | Partly | gitleaks (ADR 0006) found nothing in the app folder, the docs, this branch's commits or the whole repository history, and finds a planted fake key. `check.sh` runs it when installed and says so when skipped. No dependency or image scan yet |
| 27.1 | CI | Not met | Checks run locally with `scripts/check.sh` |
| IDP-04 3.7 | Android Lint | Partly | 0 errors, 70 warnings. Fixed: the 30 hard-coded texts moved to `strings.xml`, descriptions added to the 2 images, backup rules added. Left: 54 "text built in code" notes, 5 library-update notes, 2 button-style notes, 1 missing app icon |

## 6. Performance targets (IDP-04 3.7)

| Target | Status | Evidence |
|---|---|---|
| Offline single-page extraction under 6 s on a 6 GB phone | Not met | Measured runs in `docs/mobile-extraction/` are 35 to 100 s. On 2026-10-22 (new libraries, command line): Qwen3-VL 19 to 25 s, MiniCPM-V canvas 16 to 18 s, LFM2 22 to 29 s with a 500 px input and 85 to 95+ s with the full card (tiling; handoff section 5) |
| Cold start under 1.5 s; 60 fps lists | Not measured | |
| Base download under 30 MB (SLM via on-demand packs) | Not measured as an AAB | The minified release APK is 30.7 MB with the native libraries and without the models. The models are not packaged; they are pushed by hand |

## 7. Process and documentation

| Rule | Asks for | Status | Evidence / plan |
|---|---|---|---|
| Java Std 33.6 | An ADR for each deviation from a MUST and each new dependency | Partly | ADRs 0001–0009 written and marked Proposed; not yet approved. ADR 0009 covers the Qwen input resize, ADR 0008 the compact prompts, ADR 0007 the LFM2 input resize. ADR 0006 covers JaCoCo and gitleaks; ADR 0005 covers prompt versioning and the model registry; ADR 0004 covers the removal of PaddleOCR-VL and the test-only dependencies (JUnit, `org.json`); ADR 0003 covers ktlint and detekt |
| Java Std 33.2 | Conventional Commits with ticket | Partly | Conventional Commits used; no ticket ids |
| Java Std 33.1 | `feat/<TICKET>-<slug>` branches | Not met | Branch `mobile_standards`, by choice |
| Java Std 33.3 | PRs of about 400 lines | Deferred | No PRs until the end, by decision. Nothing is pushed |
| Java Std 34.1 | Service README with run, config, dependencies, API, operations | Partly | `mobile/idp-android/README.md` covers them; the model registry section says it is missing |
| Java Std 34.2 | Feature README | Met | `app/src/main/java/com/modeltesting/vlmtester/README.md`: for each area its files, configuration, failures and tests, plus `ARCHITECTURE.md` for the flow of a scan. The code is still one package, so there is one README |
| Ch. 26.2 | Self-check before every commit | Met | `scripts/check.sh` |
| IDP-00 1 | Raise a disagreement with a standard instead of picking silently | Met | Open questions are listed in `TL_BRIEF.md` |
