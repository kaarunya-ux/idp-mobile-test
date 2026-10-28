# 2. Internal API: additions to IDP-03 ch. 4 and the defaults we chose

Status: Proposed · Date: 2026-10-28 · Deciders: Python team lead

## Context

IDP-03 ch. 4 describes the gateway's API. Building it, and switching extraction-service's AI fallback onto it, needed
some things the document doesn't say, or says differently.

## Decisions

1. **`field-read` has a batch form.** IDP-03 has one field per call, from a crop. Extraction's fallback reads every
   unsettled field from the whole page (a card's two sides stacked) in one call, as the benchmarked web extraction
   app did; one call per field would cost 5 to 10 times more model time. The body takes either `documentType` +
   `pages` (1 or 2) + `fields` (each with optional OCR `readings`), or the original `crop` + `field` + `readings`.
   The batch answer is `{fields: [{key, value}]}` without a confidence: the model has no calibrated one, so the
   caller gives AI values its own (`AI_FALLBACK__CONFIDENCE`, ML-07).
2. **The standard card types' questions live in the gateway** (`ai/domain/card_profiles.py`, ported from
   extraction-service, which ported them from the web app). They are keyed by document type code. Other types get a
   generic question built from the field labels.
3. **`plan` is an optional body field.** Caps differ per plan (guest 10, personal 30, corporate per plan), but no
   caller knows the plan today. When it's absent, `PRIORITY` means corporate and `STANDARD` personal. To do: Java
   puts the plan in `accountPolicy`, and the callers pass it on.
4. **Caps fail open, the semaphore fails closed** when Redis is down (RDS-07: a rate limit vs. a lock). The
   semaphore protects the model server; a failed cap check costs at most some extra calls, and logs an error.
5. **A reserved call is refunded when the model never answered** (busy, breaker open, unreachable). A call that got
   an answer counts, even if the answer was unusable.
6. **One circuit breaker per replica**, for the whole model server (IDP-03 says "in-process").
7. **`table-repair` is not built.** Nothing reads tables yet (F-EX-12 comes later). Its timeout setting is added with it.
8. **`template-suggest` takes `priority` like the others** (IDP-03 leaves it out); the default is `STANDARD`.
9. **Day boundary for caps is UTC.**

## Consequences

Extraction's fallback costs one call per document. IDP-03 ch. 4.2 should get the batch form, `plan`, and the
`priority` on `template-suggest`; I'll raise it with the document owner.
