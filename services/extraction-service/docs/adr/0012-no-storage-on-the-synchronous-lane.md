# 12. No storage on the synchronous lane: images go inside the ai-gateway calls

Status: Accepted · Date: 2026-11-04 · Deciders: Python team lead (Prakyath)

## Context

The synchronous lane is an HTTP request in, OCR, the AI only when OCR leaves a field unsure, and the answer out. As
first built it stored every request's pages (and crops) in MinIO, because ai-gateway took images only as storage keys
(IDP-03 ch. 4.3). In the mixed local setup that MinIO is the backend's, on another machine: every request made network
round trips to it, even when OCR settled everything, and a request failed with 500 when that machine didn't answer.

## Decision

1. **The synchronous lane uses no storage.** A request's prepared pages and field crops are held in memory
   (`InlineImages`) under the request's folder, and dropped when it ends, whatever happened.
2. **When the AI is asked, the image goes inside the call** (`{"data": <base64>}` where a `{bucket, key}` would be;
   ai-gateway ADR 0004). When it isn't asked, nothing leaves the process.
3. The queued lane is unchanged: its files are in MinIO already, and it keeps sending keys.

## Consequences

- extraction-sync needs no storage settings. A good card photo is answered in about 1.2 to 1.8 s.
- Each AI call carries its image (a crop is small; a page some hundreds of KB). Fine on the same network; the gateway
  checks the same size limit as for stored images.
