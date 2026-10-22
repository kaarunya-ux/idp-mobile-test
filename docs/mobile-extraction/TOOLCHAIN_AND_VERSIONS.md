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

Native runtime (not Java, but the app depends on it): llama.cpp built with Android NDK 27.1.12297006 and CMake 4.1.2, `-DANDROID_PLATFORM=android-28`, ARMv8.2 with dot-product and fp16, OpenMP on, Vulkan off, shared libraries. Build script: `mobile/idp-android/native/build_llama_dotprod.sh` (hard-coded paths from another machine; edit before use). The llama.cpp commit is not recorded and the script clones the latest; pin it before a production build.

## What was changed, and where

| File | Change | Why |
|---|---|---|
| `app/src/main/java/.../ExtractionRules.kt` (new) | Date normalisation, Verhoeff (Aadhaar checksum), PAN, Aadhaar and Indian driving licence validation and the coverage grade, moved out of `MainActivity.kt` with the code unchanged. The current year is now a parameter instead of a clock read | So the rules can be tested on a plain JVM, and as a first step toward the `engine:extract` module in IDP-04 |
| `app/src/main/java/.../MainActivity.kt` | The moved functions are replaced by one-line delegates with the same names and signatures, so the other validators did not change. Run logs, OCR results and the last field canvas are written to private storage (`filesDir`) instead of the external folder. `llama-server` output is sent to logcat only on debuggable builds | Privacy: these files hold extracted document data (IDP-04 Ch. 3.8) |
| `app/src/main/res/xml/network_security_config.xml` (new) and `AndroidManifest.xml` | Cleartext HTTP is allowed to `127.0.0.1` and `localhost` only; `usesCleartextTraffic` removed | The app talks to its own local server; everything else must be HTTPS |
| `app/src/test/java/.../ExtractionRulesTest.kt` (new) | 49 JVM unit tests for the rules above | IDP-04 Ch. 3.9 |
| `app/build.gradle.kts` | Added `testImplementation` for JUnit and org.json | Needed by the tests |
| `gradle/wrapper/*`, `gradlew`, `gradlew.bat` | Gradle wrapper, copied from `mobile/idox-mobile` (same version, 8.9) | So everyone builds with the same Gradle |
| `.gitignore` | Ignores `jniLibs/`, `*.apk`, `*.aab`, `*.keystore`, `*.jks`, `.idea/`, `*.hprof` | Native libraries, models and signing keys stay out of git |

## Notes for readers coming from Java

- Kotlin sits next to Java and compiles to the same JVM bytecode. `object ExtractionRules` is a singleton (like a class with only static methods). Functions have no `public` because it is the default. `fun f(x: Int): String` is `String f(int x)`.
- Test method names are in backticks, so they read as sentences. JUnit 4 runs them as usual.
- Unit tests under `app/src/test` run on the JVM with no device or emulator.

## Running the tests

Normal route (needs a JDK 17 and the Android SDK; neither is committed to the repo):

```bash
cd mobile/idp-android
echo "sdk.dir=$HOME/Library/Android/sdk" > local.properties   # git-ignored
export JAVA_HOME=<path to a JDK 17>
./gradlew :app:testDebugUnitTest      # 49 tests
./gradlew :app:assembleDebug          # builds app/build/outputs/apk/debug/app-debug.apk
./gradlew :app:ktlintCheck :app:detekt   # style and size gates (ADR 0003)
```

Both commands were run on 2026-10-22 and succeeded (49 tests, 0 failures; the debug APK is about 44 MB with the native libraries inside). The compiler printed one warning, which was already in the code before the move: `obj.optString(k, null)` in `ExtractionRules.str`.

Local setup used on that machine, all outside the repository: Temurin JDK 17.0.20.1 in `~/Library/Android/jdk-17`; Android SDK in `~/Library/Android/sdk` with command-line tools, platform-tools 37.0.1, platform android-35 and build-tools 34.0.0. The SDK licences were accepted when it was installed. The native libraries must be present in `app/src/main/jniLibs/arm64-v8a/` (see the README) or the APK will build without them.

Before that, the rules and tests were also checked with the standalone Kotlin compiler 1.9.24 on that JDK, against JUnit 4.13.2, Hamcrest 1.3 and org.json 20240303. Four deliberate bugs placed in a scratch copy (a Verhoeff table value, the PAN entity letters, the year bound, the MEDIUM grade threshold) were each caught by at least one test.
