# 6. Coverage measurement (JaCoCo) and a secret scan (gitleaks)

Status: Proposed · Date: 2026-10-22 · Deciders: mobile extraction owner (to approve)

## Context

The Python Engineering Standard sets measurable quality gates: coverage of at least 80% of lines and 70% of branches on new code, and at least 85% on domain code (Ch. 21.2, 27.2), and a secret scan with gitleaks among the security gates (Ch. 27.1, gate 4). The compliance matrix listed coverage as "not measured" and the secret scan as "not set up". A new tool needs an ADR (Python Standard Ch. 22.3, DEP-02).

## Decision

- **JaCoCo** measures how much of the code the unit tests run. It is the coverage engine built into the Android Gradle plugin (`enableUnitTestCoverage` on the debug build type); nothing is added to the release APK. `scripts/check.sh` runs `createDebugUnitTestCoverageReport` and fails if the extracted logic falls below the floors: **85% of lines** (the domain-code figure) and **70% of branches**. `MainActivity.kt` is left out because it cannot be tested without a phone, and so are the generated view-binding class and the compiler-generated helper class, which are not our code.
- **gitleaks** scans for credentials. `check.sh` runs `gitleaks detect --no-git --source . --redact` when the tool is installed, and prints that the scan was skipped when it is not (it does not fail, so a Mac without the tool can still run the other checks). It was run once before this ADR on the app folder, the docs, the commits of this branch and the whole repository history: no findings. A deliberately planted fake key was found, so the scan works.

## Consequences

- Coverage of the extracted logic is now a number that is checked on every run (99.9% of lines and 90.1% of branches at the time of writing), and a drop below the floors blocks "done".
- The coverage figure says nothing about the part still in `MainActivity.kt` (the screen, the process and HTTP handling, bitmap work), which stays untested until a phone check can prove changes there.
- The secret scan only protects a machine that has gitleaks installed; there is no CI yet to run it for everyone.
- Dependency and image scans (gate 4 also names `pip-audit` and Trivy) are still not set up.

## Alternatives

- A coverage floor of 80% for everything: rejected, the standard asks for 85% on domain code, which this code is.
- Failing the check when gitleaks is missing: rejected for now; it would stop the whole check on a machine without the tool. A CI job should make it mandatory.
- A hand-written grep for secrets: rejected, the standard names gitleaks.
