# 3. A front-of-line lane for the synchronous path, and generic-read

Status: Proposed · Date: 2026-11-03 · Deciders: Python team lead

## Context

The backend wants two ways to extract (extraction-service ADR 0009): the queued way (RabbitMQ and MinIO) for big jobs,
and a synchronous HTTP call for one image or a card's two sides, which the model reads in about 5 seconds. A person
waits on the open request, so the model must serve it first: a batch of queued jobs (a page-read can hold a slot for
minutes) must not make that person wait or time out. The synchronous call is also generic: no template and no
document type, any document, every field (the frontend's "quick extract"). extraction-service renders PDF pages and runs OCR first; generic-read only gets
the pages of a document no template matched.

## Decisions

1. **A third priority, `INTERACTIVE`**, beside `PRIORITY` and `STANDARD`. In the shared semaphore it goes before both
   queued lanes. The queued lanes together may hold at most `MODEL__VL_MAX_CONCURRENT -
   QUEUE__INTERACTIVE_RESERVED_SLOTS` slots (default 4 - 1 = 3), so one slot is always free for a person; INTERACTIVE
   may use every slot. Within the queued lanes nothing changes (3:1).
2. **INTERACTIVE waits briefly**: `QUEUE__INTERACTIVE_WAIT_TIMEOUT` (5 s), then `AIG-503-BUSY-001` as today. The
   caller answers "busy" at once, and the backend sends the work the queued way instead of the person waiting longer.
3. **A new task, `generic-read`** (`POST /internal/v1/ai/generic-read`, prompt `generic-read.v1.md`): the document
   type as a lower snake_case code, and every field as `{key, label, type, value, confidence}`; a table is
   `{headers, rows}`. Text stays in its script (Hindi in Devanagari), never translated. Front and back are stacked into
   one picture and read as one document; a key the model lists twice is kept once. Thinking is off.
4. **Pictures are tidied for field-read and generic-read**: the white margin is trimmed and a picture whose long side
   is under `MODEL__MIN_IMAGE_SIDE_PX` (768) is enlarged, both from the benchmarked web extraction app
   (`feature/visiting_card_extraction`); for field-read this includes the single-field form, whose crop of one field
   is small. Every picture is turned upright from its EXIF orientation.
5. **field-read's single form (a crop) is what extraction now uses** for the fields its two OCR engines disagree on or
   are unsure of (extraction ADR 0009): one call per field crop, with the engines' readings as hints.

## Consequences

- With the defaults, queued work uses at most 3 of the 4 model slots even when nobody waits synchronously: some
  throughput is given up for a quick answer. Tune it with the real model server's numbers.
- A request without `plan` and with `INTERACTIVE` is capped as PERSONAL; extraction sends the plan explicitly.
- IDP-03 ch. 4.2 and 4.5 should get `INTERACTIVE` and `generic-read`; to raise with the document owner.

## Alternatives

The synchronous path calling the model server directly: nothing would share its capacity fairly or count caps, and the
two paths would compete with nobody in charge. A separate model server for the synchronous path: doubles the hardware
for a path that is idle most of the time.
