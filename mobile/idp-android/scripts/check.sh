#!/usr/bin/env bash
# One-command check for idp-android: the Android counterpart of `make check` in the Python
# Engineering Standard (Ch. 22.2 and 27.4). Run it before calling any change done.
#
#   mobile/idp-android/scripts/check.sh
#
# Needs a JDK 17 and the Android SDK; the defaults below are where they were installed on the
# development Mac (outside the repository). Override JAVA_HOME / ANDROID_HOME to use others.
set -euo pipefail
cd "$(dirname "$0")/.."

export JAVA_HOME="${JAVA_HOME:-$HOME/Library/Android/jdk-17}"
export ANDROID_HOME="${ANDROID_HOME:-$HOME/Library/Android/sdk}"

echo "== 1. no native libraries, models, packages, keys or local config tracked in git =="
forbidden=$(git ls-files . | grep -E '\.(so|gguf|bin|onnx|apk|aab|jks|keystore)$|(^|/)local\.properties$|(^|/)\.env$' || true)
if [ -n "$forbidden" ]; then
  echo "$forbidden"
  echo "FAIL: these files must stay out of git (see .gitignore)"
  exit 1
fi
echo "ok"

echo "== 1b. secret scan (gitleaks, Python Standard Ch. 27.1 gate 4) =="
if command -v gitleaks > /dev/null; then
  gitleaks detect --no-git --source . --redact --no-banner
else
  echo "gitleaks is not installed, so the secret scan was SKIPPED (brew install gitleaks)"
fi

echo "== 2. style, size, tests, Android Lint and both builds =="
./gradlew --no-daemon :app:ktlintCheck :app:detekt :app:testDebugUnitTest \
  :app:createDebugUnitTestCoverageReport :app:lintDebug :app:assembleDebug :app:assembleRelease

echo "== 3. summary =="
python3 -I - <<'PY'
import glob
import re

tests = failures = errors = 0
for path in glob.glob("app/build/test-results/testDebugUnitTest/*.xml"):
    head = open(path, encoding="utf-8").read(2000)
    tests += int(re.search(r'tests="(\d+)"', head).group(1))
    failures += int(re.search(r'failures="(\d+)"', head).group(1))
    errors += int(re.search(r'errors="(\d+)"', head).group(1))
print(f"unit tests: {tests} run, {failures} failures, {errors} errors")
lint = open("app/build/reports/lint-results-debug.txt", encoding="utf-8").read().strip().splitlines()[-1]
print(f"android lint: {lint}")

# Coverage of the extracted logic (JaCoCo, ADR 0006). MainActivity needs a phone to be tested, and the
# generated view-binding and compiler-generated classes are not our code, so they are left out.
# Floors are the Python Standard's: 85% of lines for domain code (Ch. 21.2) and 70% of branches (Ch. 27.2).
import sys
import xml.etree.ElementTree as ET

LINE_FLOOR, BRANCH_FLOOR = 85.0, 70.0
EXCLUDED = {"MainActivity.kt", "Comparisons.kt"}
report = glob.glob("app/build/reports/coverage/**/report.xml", recursive=True)[0]
totals = {"LINE": [0, 0], "BRANCH": [0, 0]}
for source in ET.parse(report).getroot().iter("sourcefile"):
    name = source.get("name")
    if name.endswith(".java") or name in EXCLUDED or name.endswith("Test.kt"):
        continue
    for counter in source.findall("counter"):
        if counter.get("type") in totals:
            totals[counter.get("type")][0] += int(counter.get("missed"))
            totals[counter.get("type")][1] += int(counter.get("covered"))
line_pct = 100 * totals["LINE"][1] / sum(totals["LINE"])
branch_pct = 100 * totals["BRANCH"][1] / sum(totals["BRANCH"])
print(f"coverage of the extracted logic: lines {line_pct:.1f}% (floor {LINE_FLOOR}%), branches {branch_pct:.1f}% (floor {BRANCH_FLOOR}%)")
if line_pct < LINE_FLOOR or branch_pct < BRANCH_FLOOR:
    print("FAIL: coverage is below the floor")
    sys.exit(1)
PY
echo "ALL CHECKS PASSED"
