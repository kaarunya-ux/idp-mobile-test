# 3. Kotlin lint and format gates for mobile extraction

Status: Proposed · Date: 2026-10-22 · Deciders: mobile extraction owner (to approve)

## Context

The Python Standard defines its gates (Ruff, mypy, limits on function length, complexity and parameters; Ch. 19, 22, 27). IDP-04 describes the Android architecture but defines no Kotlin lint, formatter or size limits. A new dependency or tool needs an ADR (Python Standard Ch. 22.3 and DEP-02; Java Standard Ch. 33.6).

## Decision

Add two Gradle plugins to `mobile/idp-android`:

- **ktlint** (`org.jlleitschuh.gradle.ktlint` 12.1.1, ktlint 1.3.1, Android Studio style) for formatting and style, configured by `.editorconfig` (4 spaces, 120 columns).
- **detekt** (`io.gitlab.arturbosch.detekt` 1.23.6) for complexity and size, configured by `config/detekt/detekt.yml`. The limits copy the Python Standard: cyclomatic complexity at most 10, functions at most 50 lines, at most 7 parameters, classes at most 400 lines, nesting at most 3 levels, no `TODO:`/`FIXME:` comments.

Run with `./gradlew :app:ktlintCheck :app:detekt`. `ktlintFormat` fixes formatting.

Scope: ktlint covers every Kotlin file except `MainActivity.kt`; detekt covers only `ExtractionRules.kt` and the tests. `MainActivity.kt` is added to both as it is split into smaller files. There is no baseline file: the code in scope passes outright.

Applying the gate meant splitting `validateAadhaar` (complexity 38), `validatePan` and `coverageReport` into smaller functions. The text they produce is unchanged and is covered by the 38 unit tests.

## Consequences

- New and moved code is checked for style and size on every build.
- The limits are borrowed from the Python Standard by analogy, not from a Kotlin standard, and should be reviewed by the Android owner.
- There is no coverage gate or CI yet; the checks run locally.
- Both tools are build-time only and add nothing to the APK.

## Alternatives

- ktlint only: no complexity or size limit.
- Android Lint only: checks Android API misuse, not style or complexity; it can be added later.
- Baselines for the old code: rejected for the in-scope files, because they were fixed instead.
