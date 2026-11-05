# 4. Images inside the request, for the synchronous lane

Status: Accepted · Date: 2026-11-04 · Deciders: Python team lead (Prakyath)

## Context

IDP-03 ch. 4.3: callers send storage keys, never bytes. extraction-service's synchronous lane keeps nothing in storage
(its ADR 0012): a request's pages and crops live in memory for the length of the request.

## Decision

Every image in a request body is either `{bucket, key}` (as before) or `{data}`: the image itself, base64. Exactly one
of the two; `data` gets the same checks as a stored image (size limit `MODEL__MAX_IMAGE_BYTES`, decodable by Pillow,
no decompression bomb). The queued lane keeps sending keys.

## Consequences

Request bodies grow by the image (a crop is small; a page some hundreds of KB). IDP-03 ch. 4.3 should get the second
form; to raise with the document owner.
