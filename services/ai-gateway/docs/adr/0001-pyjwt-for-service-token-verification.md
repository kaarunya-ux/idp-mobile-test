# 1. PyJWT verifies service tokens

Status: Proposed · Date: 2026-10-28 · Deciders: Python team lead

## Context

IDP-03 ch. 4.2: every gateway call needs a service token from `extraction-service` or `conversion-service`. SEC-01
and SEC-07 require the token to be checked against the issuer's JWKS, with `iss`, `aud`, `exp` and an RS256/ES256
allow-list. The standard's ch. 22.3 names no JWT library, so DEP-02 requires an ADR.

## Decision

Use **PyJWT** (`pyjwt[crypto]`, MIT), the same library and version range as extraction-service (its ADR 0005).
`core/security.py` accepts only tokens with `aud` = `ai-gateway` whose `azp` (the client-credentials client) is in
`AUTH__ALLOWED_CLIENTS`. User tokens are refused.

## Consequences

The same token code in both services. **Add PyJWT to the standard's approved list** when extraction's ADR 0005 is
accepted.

## Alternatives

As extraction-service ADR 0005: `python-jose`, `authlib`, or hand-written checks on `cryptography`.
