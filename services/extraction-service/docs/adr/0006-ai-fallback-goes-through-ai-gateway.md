# 6. The AI fallback goes through ai-gateway

Status: Proposed · Date: 2026-10-28 · Deciders: Python team lead · Supersedes [ADR 0004](0004-ai-fallback-calls-the-model-host-until-ai-gateway-exists.md)

## Context

ADR 0004 let the AI fallback call the model host directly until ai-gateway existed. It exists now
(`services/ai-gateway`), and IDP-03 ch. 4 says only the gateway talks to the model server.

## Decision

1. `GatewayFieldReader` calls the gateway's `field-read` in its batch form (ai-gateway ADR 0002): the document type,
   the stored page keys, and the unsettled fields with their labels. It sends a client-credentials service token
   (`SERVICE_AUTH__*`, SEC-07), the account id (for the gateway's daily caps) and the priority route the request came
   on (for its queue).
2. The worker stores the page images **before** the AI step, so the gateway can read them by key. They were stored
   after extraction before; the keys and the result are the same.
3. `ExtractionRequest` remembers whether it came on the priority route. It is stored in the request JSON with a default,
   so rows already in the database read as standard, and no migration is needed.
4. The port now takes an `AiCallContext` and returns an `AiReading` (values, model id, prompt version), because the
   gateway says per answer which model and prompt read it.
5. **A call that got no answer is not an AI call** in the result's provenance or `usage.aiCalls`. Before, a failed call
   counted. The gateway gives a failed call back to the account's daily cap, so counting it here would bill something
   that wasn't used.
6. `VisionModelFieldReader` (the web app's direct call) stays as the backup for a laptop without the gateway. It is used
   only when `ENVIRONMENT=local` and `AI_GATEWAY__URL` is unset.

## Consequences

The model server has one caller, with fair queuing, caps and a breaker in front of it. Each AI call adds one hop and a
token fetch (cached). When the gateway is busy, down or the account's cap is used, the fields stay NEEDS_REVIEW, as
before.
