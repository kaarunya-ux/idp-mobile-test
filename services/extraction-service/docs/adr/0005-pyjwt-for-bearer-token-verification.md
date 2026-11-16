# 5. PyJWT verifies bearer tokens

Status: Proposed · Date: 2026-10-28 · Deciders: Python team lead

## Context

The templates API is the first protected route of a Python service. SEC-01 requires every bearer token to be checked
against the issuer's JWKS (cached), with `iss`, `aud`, `exp`, `nbf` and an RS256/ES256 allow-list. The approved
libraries (standard ch. 22.3) name no JWT library, so DEP-02 requires an ADR.

## Decision

Use **PyJWT** (`pyjwt[crypto]`, MIT; the `crypto` extra adds `cryptography`, Apache-2.0/BSD). Its `PyJWKClient` caches
the JWKS (`AUTH__JWKS_CACHE`, default 10 min). `core/security.py` decodes with an algorithm allow-list, the issuer from
`AUTH__ISSUERS` and the audience `idp-api` (IDP-01), and maps roles to permissions (IDP-00 ch. 7.2).

## Consequences

One small, widely used library with no transitive surprises. Blocking JWKS fetches run in the thread pool (ASY-01).
**Add PyJWT to the standard's approved list** if accepted, so ai-gateway and conversion-service use the same one.

## Alternatives

`python-jose` (slower to patch, larger), `authlib` (much larger, mostly OAuth client code we don't need), writing JWT
checks by hand on `cryptography` (easy to get subtly wrong).
