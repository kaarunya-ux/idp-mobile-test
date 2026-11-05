# 11. Extraction only: the quality gate measures, tokens are optional for integration testing, two OCR threads

Status: Accepted · Date: 2026-11-04 · Deciders: Python team lead (Prakyath)

## Context

The synchronous extract must answer a good card photo in 1 to 2 seconds. The first tests showed three things in the way:
the quality gate refused readable documents (a clear A4 PDF as "glare", two good 860 px card photos as "low
resolution", because the gate wants 900 px on the short side); callers without a login yet had to fetch a test token
before every round of tests; and the two OCR engines, reading at once with 4 threads each on 8 cores, slowed each other
down (2.2 s for a card, against 1.2 s with 2 threads each). The team's decision: this service's job is extraction.
Photo checks belong where the photo is taken (the web and mobile apps), and the login belongs to the backend.

## Decision

1. **The quality gate measures, it doesn't refuse** (`QUALITY__REJECTS=false`, the new default). Blur, brightness,
   glare and resolution are still measured and recorded in the result (`pages[].quality`); nothing is refused for them.
   `true` brings back IDP-03's refusals (and PDF pages are still never judged on lighting, ADR 0010).
2. **Tokens on the synchronous routes are optional for integration testing** (`QUICK__REQUIRE_TOKEN=false` in the
   local stack only): a request without one is counted on `QUICK__DEFAULT_ACCOUNT_ID` with `QUICK__DEFAULT_PLAN`
   (CORPORATE, so tests don't run into a personal day's allowance). A request that sends a token is still checked.
   The service's default stays `true`. Keycloak stays in the stack for the services' own logins to ai-gateway.
3. **Two OCR threads per engine** (`OCR__THREADS=2`, the new default).

## Consequences

- Blurred or dark photos are read anyway; their fields come back NEEDS_REVIEW or from the AI, instead of a 422 asking
  for a better photo. The apps should keep the user-facing photo check (IDP-04 already sends them the thresholds).
- This deviates from IDP-03 ch. 2.6 stage 2 (refuse a page that fails the gate) and, for local testing only, from
  SEC-01 (a token on every route). Both are recorded here; the token deviation must not reach a shared environment:
  the stack sets it, the service's default does not.
- IDP-03 should get "measured, not refused"; to raise with the document owner.
