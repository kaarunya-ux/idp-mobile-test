# idp-android: on-device document extraction (Aadhaar / PAN)

## Purpose

Android app that extracts structured fields from ID documents on the phone, using a small vision-language model run by llama.cpp (CPU, ARMv8.2 dot-product build) and ML Kit text recognition for the OCR-first path. Owner: mobile extraction (IDP). It is a test harness today; the target architecture is IDP-04 Ch. 3 (see [ADR 0002](../../docs/adr/0002-mobile-extraction-deviations-kept-for-now.md)).

## Run locally

Requirements: Android Studio (or the Android SDK with platform 35), JDK 17, an arm64 phone.

1. Create `local.properties` with `sdk.dir=<path to your Android SDK>` (git-ignored).
2. Native libraries are not in git. Place the CPU dot-product build (`llama_dotprod/dist_dot`) in `app/src/main/jniLibs/arm64-v8a/`, renaming `llama-mtmd-cli` to `libllama-mtmd-cli.so` and `llama-server` to `libllama-server.so`. The other `.so` files keep their names. `jniLibs/` is git-ignored. `native/build_llama_dotprod.sh` rebuilds them; its paths are hard-coded to another machine and must be edited first.
3. Build and install: `./gradlew :app:installDebug`.
4. Models (`*.gguf`) are not in git. Push the model and its `mmproj` file to the app's files directory under `models/` (`adb push`). The app lists what it finds there.

## Rebuilding the native libraries

`native/build_llama_dotprod.sh` builds the llama.cpp libraries the app runs (Android arm64, ARMv8.2 with dot-product and fp16, OpenMP on, Vulkan off). It is pinned: llama.cpp is checked out at commit `000bee54a544070356b44d68af299b973d391d26` (2026-10-08), the commit recorded inside `libllama-common.so` and `libggml-base.so` of the libraries the app shipped with, with NDK 27.1.12297006 and SDK CMake 4.1.2. Nothing in it is tied to one machine; the work folder defaults to `~/llama_dotprod_build` (outside the repository) and every location and version can be overridden with an environment variable (the list is at the top of the script).

```bash
DRY_RUN=1 native/build_llama_dotprod.sh   # checks the setup, builds nothing
native/build_llama_dotprod.sh             # builds; writes build-info.txt next to the result
```

Two things to know. The script was tested here with stand-in compiler tools only (this Mac has no NDK): the setup checks, the pinned checkout and the flags were verified, the real compile was not. It builds `llama-server` and `llama-bench`, as the original did; the app's one-shot path also runs `llama-mtmd-cli`, so add it with `BUILD_TARGETS="llama-server llama-bench llama-mtmd-cli"`. The executables must then be renamed `libllama-server.so` and `libllama-mtmd-cli.so` when copied into `app/src/main/jniLibs/arm64-v8a/`.

## How the code is organized

See `docs/mobile-extraction/ARCHITECTURE.md`: the files, the flow of a scan, and the rules for new code.

## Checking a change

`scripts/check.sh` runs everything in one go: no forbidden files tracked in git, a secret scan (gitleaks, when installed: `brew install gitleaks`), ktlint, detekt, the unit tests with coverage, Android Lint, and the debug and release builds. It prints the test count, the Lint summary and the coverage, and fails if coverage of the extracted logic drops below 85% of lines or 70% of branches. Rule-by-rule status is in `docs/mobile-extraction/STANDARDS_COMPLIANCE.md`.

## Reading run logs

`run_log.txt`, `ocr_results.txt` and `last_canvas.jpg` are in the app's private storage because they contain extracted document data. On a debug build: `adb shell run-as com.modeltesting.vlmtester cat files/run_log.txt`. Models and OCR input images (`ocr_in/`) stay in the external files folder, so `adb push` still works.

## Configuration

There are no environment variables. Per-model settings (prompts, grammars, schemas, image-token caps, context size) are in `app/src/main/assets/` and in `MainActivity.kt`. Moving the thresholds out of code is tracked in ADR 0002.

## Dependencies

- ML Kit `text-recognition` 16.0.1 (bundled Latin model).
- llama.cpp native build (`libllama-server.so`, `libllama-mtmd-cli.so` and the shared libraries), run as a local process on `127.0.0.1`.
- No remote services and no IDP API calls yet.

## API

None yet. Future contract: `idp-contracts` (IDP-00 Ch. 7, IDP-04). The generated client is a later step.

## Model registry

The Python Standard (ML-01) requires a registry with name, version, source and SHA-256 for every model. This app does not have one yet. Add one before release.

## Operations

Not released. Open items before production: signed minified release build, Play Asset Delivery for models, privacy fixes for stored logs, tests, and a pinned llama.cpp commit for the native build. All are tracked in ADR 0002.
