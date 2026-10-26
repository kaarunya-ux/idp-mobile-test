# Mobile extraction: toolchain, versions and what changed in Kotlin/Java

For developers who know Java but not this app. Scope: `mobile/idp-android/` only. There is no Java service code in this repository; the Java platform and services described in IDP-01/02 live elsewhere. `mobile/idox-mobile/` is Java, belongs to the conversion work, and has not been touched.

## Versions in use

| Item | Version | Where it is set |
|---|---|---|
| Android Gradle Plugin | 8.5.2 | `mobile/idp-android/build.gradle.kts` |
| Kotlin | 1.9.24 (IDP-04 asks for 2.x; see ADR 0002 row 3) | same |
| Gradle | 8.9 (wrapper, `./gradlew`) | `gradle/wrapper/gradle-wrapper.properties` |
| JDK | 17 (`sourceCompatibility`, `targetCompatibility`, `jvmTarget`) | `app/build.gradle.kts` |
| compileSdk / targetSdk | 35 | `app/build.gradle.kts` |
| minSdk | 28 (the native libraries are built for android-28; IDP-04 asks for 26; see ADR 0002 row 2) | `app/build.gradle.kts` |
| AndroidX core-ktx / appcompat / activity-ktx / lifecycle-runtime-ktx | 1.13.1 / 1.7.0 / 1.9.2 / 2.8.4 | `app/build.gradle.kts` |
| Material Components | 1.12.0 | same |
| ML Kit text recognition (bundled Latin model) | 16.0.1 | same |
| ktlint plugin / ktlint | 12.1.1 / 1.3.1 | `build.gradle.kts`, `app/build.gradle.kts` |
| detekt | 1.23.6 | same, rules in `config/detekt/detekt.yml` |
| JUnit (tests) | 4.13.2 | same |
| org.json (tests) | 20240303 | same |

Native runtime (not Java, but the app depends on it): llama.cpp built with Android NDK 27.1.12297006 and CMake 4.1.2, `-DANDROID_PLATFORM=android-28`, ARMv8.2 with dot-product and fp16, OpenMP on, Vulkan off, shared libraries. Build script: `mobile/idp-android/native/build_llama_dotprod.sh`, now pinned to llama.cpp commit `000bee54a544070356b44d68af299b973d391d26` (2026-10-08, the commit found inside the shipped libraries) and free of machine-specific paths; see the README. Tested with stand-in tools only, because this Mac has no NDK.

## What was changed, and where

| File | Change | Why |
|---|---|---|
| `app/src/main/java/.../ExtractionRules.kt`, `PanRules.kt`, `AadhaarRules.kt`, `DlRules.kt`, `PassportRules.kt` (new) | `ExtractionRules` holds the shared pieces (date normalisation, Verhoeff checksum, coverage grade). The other four hold the PAN, Aadhaar, Indian driving licence and Indian passport checks. All were moved out of `MainActivity.kt` with the code unchanged; the calendar year is now a parameter instead of a clock read. Split into one file per document so no file passes 400 lines | So the rules can be tested on a plain JVM, and as a first step toward the `engine:extract` module in IDP-04 |
| `app/src/main/java/.../ModelProfiles.kt`, `ImageSizing.kt` (new) | `ModelProfiles`: which model is loaded (Qwen, LFM2, MiniCPM, detected by file name), which per-document profile applies (PAN and Aadhaar with Qwen, PAN with LFM2), the measured constants (card width 480, crop limit 1000, server cap 1024, LFM2 cap 128) and the image-token cap rule. `ImageSizing`: the resize arithmetic (when to shrink and to what size). Both moved out of `MainActivity.kt` with the values and the measurement comments unchanged. PaddleOCR-VL handling deliberately stays in `MainActivity.kt` | Same reason as above: testable on a plain JVM, and the `engine:slm` module in IDP-04 |
| `app/src/main/java/.../RunLog.kt`, `ModelFiles.kt`, `ByteFormat.kt` (new) | `RunLog` appends run and status blocks to the run log (plain text, private storage). `ModelFiles` splits the pushed `.gguf` files into models and `mmproj-` projectors and decides whether a projector belongs to a model. `ByteFormat` formats the storage and RAM sizes. Moved out of `MainActivity.kt` with the same output. The run counter and the spinners stay in the activity because they need Android | The `data` layer of IDP-04, testable on a plain JVM |
| `app/src/main/java/.../OutputParser.kt`, `Stage1Rules.kt`, `ProcessStats.kt` (new) | `OutputParser`: reads the model's text into JSON, rejects output copied from our own prompts, strips llama.cpp's startup log from the answer. `Stage1Rules`: the lighter PAN and Aadhaar checks for the stage1 fine-tune. `ProcessStats`: memory and CPU figures from `/proc` text. `PanRules` gained the single-field crop check. All moved out of `MainActivity.kt`; a parse failure is now caught as `JSONException` instead of any `Exception` | Same reason as above |
| `app/build.gradle.kts`, `app/proguard-rules.pro` | The release build now minifies and shrinks resources with R8 (unsigned; about 31 MB instead of 40 MB). Not yet run on a phone | IDP-04 Ch. 3.7 size target |
| `app/src/main/java/.../LlamaLaunch.kt`, `LlamaRequest.kt` (new) | `LlamaLaunch`: the `llama-mtmd-cli` and `llama-server` command lines and the process environment (core pinning, 4096 context cap, one slot, prompt-cache limit, image-token cap). `LlamaRequest`: the JSON sent to the server's chat endpoint, the warm-up request, and the reading of the reply (answer text, usage figures). Both moved out of `MainActivity.kt` flag for flag; the measurement notes moved with them. The process start and the HTTP calls stay in the activity | LLM-01: the model call's contract in one tested place |
| `app/src/main/java/.../ExtractionPlan.kt` (new) | For a single-image scan: how the image is prepared, which prompt, grammar and schema go in, the token cap and output budget, and how the answer is checked, all decided from the document type and the model. Moved out of `runExtraction` unchanged; the two-sided flow is not covered | Extraction-flow decisions in one tested place |
| `app/src/main/java/.../PromptFile.kt`, `ModelRegistry.kt` (new), `assets/prompts/*.v1.md`, `assets/models.yaml` | Prompts are now `<name>.v1.md` with front matter (model, temperature, schema); `PromptFile` strips the front matter and builds the `promptVersion` fields of the run-log header. `models.yaml` is the model registry and `ModelRegistry` its strict reader. The prompt texts are unchanged (a test pins the SHA-256 of every body) | LLM-02, ML-01, ML-04; ADR 0005 |
| `app/src/main/java/.../GpuSupport.kt` (new), `MainActivity.kt` (`setupGpuSwitch`) | The "Use GPU (Vulkan)" switch is disabled, with an explanation, when the shipped native libraries contain no Vulkan backend (they do not: the build is CPU only). Before, it could be switched on while the model still ran on the CPU and the run log said GPU | ML-10: never fall back silently |
| `app/build.gradle.kts` (`enableUnitTestCoverage`), `scripts/check.sh`, `res/values/strings.xml`, `res/xml/data_extraction_rules.xml` | JaCoCo coverage of the unit tests with floors in `check.sh`; a gitleaks step; the screen's 30 texts moved out of the layout into `strings.xml` (same words), 2 image descriptions, and backup/transfer rules for Android 12+ | ADR 0006; IDP-04 Ch. 3.8 |
| PaddleOCR-VL | Support removed (ADR 0004) from `MainActivity.kt` (detection, `--jinja` and prompt folding, its image-size environment variables) at the owner's request, 2026-10-22. Findings docs and logs are unchanged | Not needed |
| `app/src/main/java/.../MainActivity.kt` | The moved functions are replaced by one-line delegates with the same names and signatures, so the other validators did not change. Run logs, OCR results and the last field canvas are written to private storage (`filesDir`) instead of the external folder. `llama-server` output is sent to logcat only on debuggable builds | Privacy: these files hold extracted document data (IDP-04 Ch. 3.8) |
| `app/src/main/res/xml/network_security_config.xml` (new) and `AndroidManifest.xml` | Cleartext HTTP is allowed to `127.0.0.1` and `localhost` only; `usesCleartextTraffic` removed | The app talks to its own local server; everything else must be HTTPS |
| `app/src/test/java/.../*RulesTest.kt`, `TestJson.kt` (new) | 226 JVM unit tests for the rules above | IDP-04 Ch. 3.9 |
| `app/build.gradle.kts` | Added `testImplementation` for JUnit and org.json | Needed by the tests |
| `gradle/wrapper/*`, `gradlew`, `gradlew.bat` | Gradle wrapper, copied from `mobile/idox-mobile` (same version, 8.9) | So everyone builds with the same Gradle |
| `.gitignore` | Ignores `jniLibs/`, `*.apk`, `*.aab`, `*.keystore`, `*.jks`, `.idea/`, `*.hprof` | Native libraries, models and signing keys stay out of git |

## Notes for readers coming from Java

- Kotlin sits next to Java and compiles to the same JVM bytecode. `object ExtractionRules` is a singleton (like a class with only static methods). Functions have no `public` because it is the default. `fun f(x: Int): String` is `String f(int x)`.
- Test method names are in backticks, so they read as sentences. JUnit 4 runs them as usual.
- Unit tests under `app/src/test` run on the JVM with no device or emulator.

## One-command check

`mobile/idp-android/scripts/check.sh` runs the file check, ktlint, detekt, the unit tests, Android Lint and both builds. It is the Android counterpart of `make check` in the Python Standard.

## Running the tests

Normal route (needs a JDK 17 and the Android SDK; neither is committed to the repo):

```bash
cd mobile/idp-android
echo "sdk.dir=$HOME/Library/Android/sdk" > local.properties   # git-ignored
export JAVA_HOME=<path to a JDK 17>
./gradlew :app:testDebugUnitTest      # 226 tests
./gradlew :app:assembleRelease        # minified, unsigned release APK
./gradlew :app:lintDebug              # Android Lint: 0 errors, 70 warnings
./gradlew :app:assembleDebug          # builds app/build/outputs/apk/debug/app-debug.apk
./gradlew :app:ktlintCheck :app:detekt   # style and size gates (ADR 0003)
./gradlew :app:createDebugUnitTestCoverageReport   # coverage; report in app/build/reports/coverage (ADR 0006)
```

Both commands were run on 2026-10-22 and succeeded (226 tests, 0 failures; the debug APK is about 44 MB with the native libraries inside). The compiler printed one warning, which was already in the code before the move: `obj.optString(k, null)` in `ExtractionRules.str`.

Local setup used on that machine, all outside the repository: Temurin JDK 17.0.20.1 in `~/Library/Android/jdk-17`; Android SDK in `~/Library/Android/sdk` with command-line tools, platform-tools 37.0.1, platform android-35 and build-tools 34.0.0. The SDK licences were accepted when it was installed. The native libraries must be present in `app/src/main/jniLibs/arm64-v8a/` (see the README) or the APK will build without them.

Before that, the rules and tests were also checked with the standalone Kotlin compiler 1.9.24 on that JDK, against JUnit 4.13.2, Hamcrest 1.3 and org.json 20240303. Four deliberate bugs placed in a scratch copy (a Verhoeff table value, the PAN entity letters, the year bound, the MEDIUM grade threshold) were each caught by at least one test.
