# 9. A synchronous extract beside the queued path, and the AI step on field crops

Status: Proposed · Date: 2026-11-03 · Deciders: Python team lead

## Context

IDP-03 has one way in: the Java backend uploads to MinIO and sends `ExtractionRequested` on RabbitMQ, and the result
comes back as `ExtractionCompleted`. For one picture, or a card's two sides, the whole read takes seconds; the queue,
the storage round trips and the polling add delay for nothing. The frontend also wants to extract documents it has no
template for ("quick extract": any type, any country, English and Hindi). The backend has written its client against
a direct HTTP call (`POST /api/v1/extract`, multipart `file` and `back`).

The reading itself is the same on both paths (the team's plan): two OCR engines read the document first; where their
two readings disagree, or are below the confidence threshold, only that part is cut out and sent to the AI. Until now
the AI step sent the whole page with every unsettled field; a crop of one field is a smaller, clearer question.

## Decision

1. **Two paths, chosen by the backend.** The queued path stays as it is (templates, batches, long PDFs). A new
   synchronous route, `POST /api/v1/extract`, answers on the open request.
2. **The synchronous route runs in its own process, `extraction-sync`** (port 8003), from the worker image: it needs
   the OCR engines, which the slim API process must not load (ML-09). It loads them once and runs each request's
   pipeline in a worker thread, at most `QUICK__MAX_CONCURRENT` (2) at once.
3. **OCR first, on both paths.** The quality gate, both engines, the template's fields and rules, exactly as the
   worker runs them. Then **the AI step reads crops**: each field still NEEDS_REVIEW (the engines disagreed, agreed
   below `REVIEW__CONFIDENCE_THRESHOLD`, or missed a required field) is cut out of the page (the area the engines read
   it in, moved with its anchor, with a margin of `AI_GATEWAY__CROP_PADDING` field heights), stored, and read alone
   through ai-gateway's `field-read` single form, with both engines' readings as hints; up to
   `AI_GATEWAY__CROP_CONCURRENCY` (3) crops at once. An AI value still has to pass the field's rule.
   `AI_GATEWAY__READ_MODE=page` keeps the earlier whole-page request, to compare the two on real documents.
4. **A document no template matches** (keywords, then the AI's classify) goes to ai-gateway's `generic-read`: its type
   and every field it shows, tables as `{headers, rows}`. There are no fields to crop without a template.
5. **The model is reached only through ai-gateway**, on its INTERACTIVE lane (ai-gateway ADR 0003): served before
   queued work, with a slot queued work may not take. When it has no slot within 5 s, or every synchronous slot here
   is taken, the route answers at once `429 EXT-429-BUSY-001`: the backend sends the document the queued way. The
   daily AI cap is a different code (`429 EXT-429-AI-001`): sending it queued won't help.
6. **What it takes**: one PDF alone of at most `QUICK__MAX_PAGES` (2) pages, or a JPG/PNG/WebP front and optionally its
   back; each file up to 20 MiB, both up to 30 MiB, the type from the content. Quality problems answer at once with
   the pipeline's codes (`EXT-422-QUALITY-00x`), so the person can retake the photo.
7. **Nothing is kept.** The prepared pages and crops live under `<accountId>/quick/<id>/` in the derived bucket only
   while ai-gateway reads them (it takes keys, never bytes), and the folder is deleted after the answer.
8. **The token is the person's**, forwarded by the backend: the account from `account_id` (SEC-05), the permission
   `document:write` (every role, IDP-00 ch. 7.2), the plan for the daily AI allowance from the roles.
9. **A second synchronous route, `POST /api/v1/extract/generic`, goes straight to the model**: the pages are only
   prepared (a PDF's rendered, a photo turned upright) and read whole by `generic-read`; no OCR and no quality gate.
   It is for documents the backend knows have no template, or to compare against the OCR-first route. Same inputs,
   limits, answer shape (`readBy: AI`) and busy rule.
10. **Our conventions on the wire**: camelCase, confidences 0 to 1. Each field says how it was read (`status`,
   `source`: OCR_AGREE, AI...), and the answer says who read the document (`readBy`: OCR, OCR_AND_AI or AI).

## Consequences

- New error codes (added to IDP-00 ch. 7.4, nothing changed): `EXT-413-FILE-001`, `EXT-415-FILE-001`,
  `EXT-429-BUSY-001`, `EXT-429-AI-001`.
- New dependency: `python-multipart` (Apache-2.0), which FastAPI needs to read uploads; not on the standard's ch. 22.3
  list, this ADR is its record.
- One more process to run and watch; it is the worker image with a different command.
- The AI step costs one model call per unsure field instead of one per document (ai-gateway ADR 0002 chose the batch
  for that reason). Crops are small and quick, and a well-read document sends none; if the model server's slots run
  short, a multi-crop form of `field-read` is the next step. Compare both modes before tuning (ML-07).
- The generic extraction is not on the queued path yet (`templateId: "generic"` in `ExtractionRequested`): it needs
  table fields in `result.json` (IDP-00 ch. 7.8), a contract change to agree with the backend first.
- IDP-03 ch. 2 should get the synchronous route and the crop step; to raise with the document owner.

## Alternatives

- **Queued only (IDP-03 as written)**: correct but slow for a single picture.
- **The synchronous route straight to the model** (as first built): skips OCR, the layer the team relies on first.
- **The API process puts the job on a Celery queue and waits**: needs a result store and holds a connection per
  request across a queue hop; the separate process does the same work with less in between.
- **The backend calls the model or the old Mac mini app directly**: no token check, no daily caps, no queue.
