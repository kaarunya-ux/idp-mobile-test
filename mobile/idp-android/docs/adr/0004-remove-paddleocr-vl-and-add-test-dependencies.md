# 4. Remove PaddleOCR-VL support from the app; add test-only dependencies

Status: Proposed · Date: 2026-10-22 · Deciders: mobile extraction owner (to approve)

## Context

Two changes made on this branch each need a record (Java Standard Ch. 33.6, Python Standard Ch. 22.3 and DEP-02):

1. `mobile/idp-android` carried special handling for PaddleOCR-VL, one of the models tried while the app was a model test harness: detection by file name, a `--jinja` flag, a folded system prompt, and two image-size environment variables. The team tested the model and decided it is not needed.
2. The unit tests need libraries that the app itself does not ship.

## Decision

1. **Remove the PaddleOCR-VL handling from `MainActivity.kt`** (done at the owner's request, 2026-10-22). Nothing else changes: the other models (Qwen3-VL 2B and 4B, LFM2-VL, MiniCPM-V, VisionPsy-Nano) keep their behavior. A PaddleOCR-VL model file pushed to the phone would now run with the generic settings, not with its former special case. The measurements and findings that mention it stay in `docs/mobile-extraction/` as history.
2. **Add test-only dependencies** to `app/build.gradle.kts`: JUnit 4.13.2 and `org.json:json` 20240303 (needed because the Android copy of `org.json` throws "not mocked" in a plain JVM test). They are on the `testImplementation` classpath and are not packaged into the APK. The build and lint tools (ktlint, detekt) are covered by ADR 0003.

## Consequences

- Less code to maintain and no unused model branches in the launch and request code.
- If PaddleOCR-VL is ever needed again, its handling must be re-added; the old code is in git history (commit `834025f` and earlier).
- The test libraries are widely used and carry no runtime risk. Their versions are pinned.

## Alternatives

- Keep the PaddleOCR-VL branches "just in case": rejected by the owner; unused model code makes the launch and request logic harder to read and test.
- Mock `org.json` in the tests: rejected; it hides real parsing behavior, and the point of the tests is to run the real code.
