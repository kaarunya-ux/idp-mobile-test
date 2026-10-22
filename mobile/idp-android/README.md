# idp-android: on-device document extraction (Aadhaar / PAN)

## Purpose

Android app that extracts structured fields from ID documents on the phone, using a small vision-language model run by llama.cpp (CPU, ARMv8.2 dot-product build) and ML Kit text recognition for the OCR-first path. Owner: mobile extraction (IDP). It is a test harness today; the target architecture is IDP-04 Ch. 3 (see [ADR 0002](../../docs/adr/0002-mobile-extraction-deviations-kept-for-now.md)).

## Run locally

Requirements: Android Studio (or the Android SDK with platform 35), JDK 17, an arm64 phone.

1. Create `local.properties` with `sdk.dir=<path to your Android SDK>` (git-ignored).
2. Native libraries are not in git. Place the CPU dot-product build (`llama_dotprod/dist_dot`) in `app/src/main/jniLibs/arm64-v8a/`, renaming `llama-mtmd-cli` to `libllama-mtmd-cli.so` and `llama-server` to `libllama-server.so`. The other `.so` files keep their names. `jniLibs/` is git-ignored. `native/build_llama_dotprod.sh` rebuilds them; its paths are hard-coded to another machine and must be edited first.
3. Build and install: `./gradlew :app:installDebug`.
4. Models (`*.gguf`) are not in git. Push the model and its `mmproj` file to the app's files directory under `models/` (`adb push`). The app lists what it finds there.

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
