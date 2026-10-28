# 7. Build step E6: recheck, deep analysis, the designer, the bundle, and what the documents left open

Status: Proposed · Date: 2026-10-28 · Deciders: Python team lead (contract items need the Java team)

## Context

E6 finishes extraction-service against IDP-03 ch. 2. Several pieces have no contract yet, or the documents disagree
with what can be built. Each choice below is the default we took; the ones marked **Java** need the Java team's
agreement before production.

## Decisions

### Contracts

1. **RECHECK arrives as `ExtractionRequested` with `options.recheck = {result: {bucket, key}, fields: [...]}`.**
   **Java.** IDP-00 ch. 7.6 has no `RecheckRequested`, and IDP-02 ch. 3.6 gives RECHECK `options {resultId, fields}`,
   but a result id means nothing to this service: it needs the on-device result's key. `files` is the uploaded
   original, as for an extraction. Empty `fields` means "every field not ACCEPTED with enough confidence".
2. **Deep analysis takes the document id from `resultKey`** (`<accountId>/<documentId>/<jobItemId>/result.json`,
   IDP-00 ch. 7.7), because `DeepAnalysisRequested` has no `documentId`. **Java:** add `documentId` to the payload.
   The analysed result is this item's own `result.json`; `ExtractionCompleted` names it and counts 1 AI call
   (IDP-02 counts 5 for billing; that is Java's business).
3. **Every key in a message must start with the message's `tenantId`** (recheck result, deep-analysis result and
   pages). Another account's key is refused with `WorkFailed`, never read (SEC-05).
4. **Samples are `{files: [{bucket, key}], expected}`**, not document ids: this service can't resolve a
   document-service id. **Java:** the designer UI sends the uploaded file's key, which document-service knows.
   Files come from `idp-originals` or `idp-templates` and, for an organisation's template, from its own prefix.
5. **`AccountChanged` is not consumed**: the masking policy arrives with every request. `AccountDeleted` archives the
   account's templates (never deleted: results refer to them), switches off its document types, and deletes its
   samples in `idp-templates`. No inbox: doing it twice is harmless.

### Behaviour

6. **A request that names a template uses it without classifying** (IDP-03 ch. 2.6 stage 4: "use templateId if
   given"). Before E6 the named template still had to match its keywords.
7. **AI classify only for unknown pages**, and only with ai-gateway: below `AI_GATEWAY__CLASSIFY_MIN_CONFIDENCE`
   (0.8) the page stays unknown. Keyword ties are not sent to the AI (no case seen yet).
8. **More than `AI_GATEWAY__MAX_FIELDS_PER_DOCUMENT` (10) unsettled fields: no AI call**, the fields go to review
   (IDP-03 ch. 2.7 names the setting `AI__…`; it lives with the other gateway settings).
9. **Recheck runs every stage on the original**, then keeps the device's settled fields. IDP-03 says "stages 2-8
   only for fields below the threshold"; the engines read whole pages anyway, so reading only some fields saves
   nothing. If the server sees another document type, its result wins whole (`recheck.documentTypeChanged`).
10. **Test runs don't use AI** and aren't billed: they score the template, not the model. A sample that can't be
    read (a blurred photo) is listed in `failedSamples` and not scored. Values compare without case, spaces or
    punctuation; masked fields compare their last digits. **Confusions hold sample values** and are shown only to
    the template's account.
11. **A test run counts for publishing only for the exact definition it tested** (a hash), from the last 24 hours,
    with every required field ≥ `TEMPLATES__PUBLISH_MIN_ACCURACY` (0.9). Otherwise publishing still needs a reason.
12. **The bundle version is a hash of its content** (published definitions, their rule ids, the thresholds), so it
    changes exactly when the phone must download again. `ETag` and `?since=` both answer 304.
13. **Suggestions read the sample as a worker prepared it** (page PNG and engine A's words in `idp-templates`): the
    API image has no OCR. ai-gateway now reads `idp-templates` too.
14. **New error codes:** `EXT-503-AI-001` (deep analysis or suggestions without an AI answer; retryable),
    `EXT-409-SAMPLE-001` (sample not prepared), `EXT-409-SAMPLE-002` (no samples to test),
    `EXT-404-TESTRUN-001`.

### Dependencies

15. **`prometheus-client`, `celery` and `boto3` move into the main dependencies**: the API now serves `/metrics`,
    sends designer tasks and reads prepared samples. All three are on the standard's approved list.

## Not built

Generic key-value and tables for unknown layouts (F-EX-12), a Celery pool per OCR engine
(IDP-03's canvas; a scaling change, ADR 0003 item 10), Eureka registration (no approved Python client), the
evaluation harness and golden sets (they need labelled real documents, which stay local), the 90-day cleanup of
`pipeline_runs`, OpenTelemetry tracing.

Office text reading came right after, in ADR 0008.

## Consequences

extraction-service does every job type IDP-00 gives it. Items 1, 2 and 4 must be agreed with the Java team; until
then Java can't send RECHECK or designer samples in this shape.
