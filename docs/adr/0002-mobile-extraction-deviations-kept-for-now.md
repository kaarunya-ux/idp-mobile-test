# 2. Deviations from IDP-04 kept for now (mobile extraction)

Status: Proposed · Date: 2026-10-22 · Deciders: mobile extraction owner (to approve)

## Context

`mobile/idp-android` is a test harness that predates the standards. Moving it to the IDP-04 architecture is staged. Until each stage lands, these rules are knowingly not met. Python Standard Ch. 0.3 and Java Standard Ch. 33.6 require an ADR for deviating from a MUST.

## Decision

| # | Rule (source) | Current state | Plan |
|---|---|---|---|
| 1 | HTTPS only to API and MinIO hosts (IDP-04 Ch. 3.8) | `usesCleartextTraffic="true"`; the app talks to its own `llama-server` on `127.0.0.1` | Replace with a network security config permitting cleartext to `127.0.0.1` only. The app has no remote API calls yet |
| 2 | Min SDK 26 (IDP-04 Ch. 3.1) | Min SDK 28, because the native llama.cpp libraries are built with `-DANDROID_PLATFORM=android-28` (`native/build_llama_dotprod.sh`) | Keep 28. Meeting 26 means rebuilding the libraries and re-testing; revisit only if the product needs devices below Android 9 |
| 3 | Kotlin 2.x, Compose, MVVM, Hilt, Room + SQLCipher, WorkManager, Retrofit (IDP-04 Ch. 3.1–3.2) | Kotlin 1.9.24, view binding, a single 2,660-line activity | Behaviour-preserving split into `ui`/`domain`/`data`/`engine` first; stack migration after the contracts repo exists |
| 4 | No document content in logs; tokens and DB key in Keystore (IDP-04 Ch. 3.8) | Run logs and OCR results are written to external storage in plain text | Move to private storage and remove document content (planned fix) |
| 5 | Tests for view models, rules, quality gate (IDP-04 Ch. 3.9) | None | Add rule and quality-gate tests before restructuring |
| 6 | Kotlin lint, format and coverage gates | The standards define none for Kotlin | Propose ktlint and detekt; to be approved separately |
| 7 | Models delivered via Play Asset Delivery (IDP-04 Ch. 3.1) | Models copied to the app's files directory by hand | Close before release |

## Consequences

- Each row has an owner (the mobile extraction owner) and is closed by a later change on this branch. Rows 1 and 4 are done first because they affect privacy.
- Anyone reading the code can tell a deliberate gap from an oversight.
- This ADR is revised as rows close.

## Alternatives

Fix everything at once: rejected. A big-bang rewrite cannot be checked against the current behaviour without a build and a device.
