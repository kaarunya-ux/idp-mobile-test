# 2. Synchronous conversion and merge beside the queued path, with the libraries and, for scans and photos, the AI

Status: Proposed · Date: 2026-11-06, amended 2026-11-07 (scans and photos read) · Deciders: Python team lead (Prakyath)

## Context

IDP-03 has one way into this service: the backend uploads to MinIO and sends `ConversionRequested`, and the result comes
back as `ConversionCompleted`. A small digital PDF converts in a second or two (8 pages to Word: 1.5 s), so the queue and
the storage round trips add delay for nothing. The backend asked for an HTTP route and will choose per file between it
and the queue, as it already does for extraction (extraction ADR 0009).

## Decision

1. **`POST /api/v1/convert` and `POST /api/v1/merge`, in their own process, `conversion-sync`** (port 8004), from the
   worker image. `/convert` takes one or more `file`, a `target`, and optionally `options` (JSON, the same names as a
   job's options). The answer is the converted file itself, with `X-Page-Count`. `/merge` takes PDFs and images and
   answers one PDF, in the order sent.
2. **Several files.** Several images to a PDF or a deck become one file, as on a job. Any other several files are
   converted one by one and come back as one ZIP, named after each file; so does one file that makes several (a PDF's
   pages as images). If one of them can't be converted, the request fails and the error names the file. Files in one
   request run one after another, in one worker process.
3. **The libraries, and the AI for a scan or a photo.** A photo (PNG, JPEG) or a scanned PDF to Word, Excel or CSV is
   read while the caller waits, by the job path's own reader (`AI_GATEWAY__PAGE_READER`, 4 pages at a time; about 12 s
   a photo and 18 s a 3-page scan on the 35B), when ai-gateway is configured; without it, it answers
   `CNV-422-SYNC-001`. A scan with more pages to read than `SYNC__MAX_READ_PAGES` (5; the `pageRange` if one is given)
   is refused at once with `CNV-422-SYNC-001`: send it as a job, or fewer pages. Drawing a scanned PDF's pages as
   images reads nothing, so it is never limited. An import contract keeps the routes away from `httpx` and
   `idp_ocr_engines`. (Until 2026-11-07 every scan and photo was refused; the owner asked for them so the backend can
   test every conversion here.)
4. **The backend decides how much to send.** Our limits are only ceilings that protect the machine (FIL-05), the same as
   the queued way: `SYNC__MAX_FILE_SIZE` (200 MiB), `SYNC__MAX_TOTAL_SIZE` (1 GiB), `SYNC__MAX_FILES` (200) answer
   `CNV-413-FILE-001`, and `LIMITS__MAX_PAGES` (1000) answers `CNV-422-LIMIT-001`. `SYNC__MAX_CONCURRENT` (4) requests run
   at once; one more answers `CNV-429-BUSY-001` immediately. A large file holds its slot, and its caller, for as long as
   it takes.
5. **Each conversion runs in a worker process** (`ProcessPoolExecutor`, standard ch. 15), spawned at startup. PDFium can't
   be used from two threads at once, the work holds the GIL, and a crash in a native library loses one request: the
   pool is replaced and the next request is served.
6. **One LibreOffice is kept started per worker process** (`OFFICE__KEEP_WARM`, set by this process only; queued jobs are
   unchanged). Most of a LibreOffice run is its start (about 2.5 s). A document handed to one already started took a
   median 0.37 s over 18 documents. Each spare takes one document and is replaced behind the answer, on a new throwaway
   profile, so a damaged profile still never carries over. With no spare ready, or a handoff that makes nothing, the
   document takes the ordinary run. IDP-03 plans a `unoserver` pool; this needs no new dependency and no UNO Python.
7. **The type comes from the content** (FIL-03): signatures, then the main part inside an Office file's ZIP; text with no
   NUL byte is taken as CSV. The caller's file name is used only for the outputs' names, made safe.
8. **The token is the person's**, as on extraction's route: the permission `document:write`. `SYNC__REQUIRE_TOKEN=false`
   lets a request in without one for integration testing (as extraction's ADR 0011); a token that is sent is still
   checked. A scan's or photo's AI calls are counted on the token's account, or on `SYNC__DEFAULT_ACCOUNT_ID` for a
   tokenless request; with neither, a scan or photo to a document answers `CNV-403-AUTH-001`. The other conversions
   need no account.
9. **Errors are RFC 9457 problem details**, with the same codes a `WorkFailed` carries. `DomainError` can now be rebuilt
   from its detail and attributes, because these errors come back from a worker process.

## Deviations, for the owner to accept

- **IDP-03: "no public API".** This is the second way in, as extraction's ADR 0009 was for that service.
- **FIL-01, FIL-02, FIL-07 (bytes through the API process):** files pass through this process, up to the ceilings above,
  in a temporary folder removed after the answer. A client that disconnects mid-download can leave its folder behind until
  the container restarts.
- **FIL-04 (virus scan):** these routes do not scan. On the queued path the backend's upload comes first; on this one the
  backend must scan before it calls.
- **FIL-05 (a timeout on every parser):** LibreOffice has its timeout, but PDF reading has none here. A file that hangs a
  worker holds that slot until the process restarts.

## Consequences

- New error codes (added, nothing changed): `CNV-400-REQUEST-001`, `CNV-401-AUTH-001`, `CNV-403-AUTH-001`,
  `CNV-413-FILE-001`, `CNV-415-FILE-001`, `CNV-422-SYNC-001`, `CNV-429-BUSY-001`.
- About 100 MB of memory per worker process for its spare LibreOffice (383 MB for the 4, measured in Docker).
- A worker process killed outright leaves its spare running until the container restarts.
- The worker image runs under `tini` (Debian package, MIT): LibreOffice's leftover helper processes pass to PID 1, and
  without an init they stay as zombies, about 6 per document as measured in Docker.
- New dependencies, in the `workers` extra only, the same as extraction-service's: `python-multipart` (Apache-2.0) and
  `pyjwt[crypto]` (MIT).
- `core/security.py` and `core/problem_details.py` are copies of extraction's, until the shared `idp-py-platform` package
  exists.
- One more process to run and watch; it is the worker image with a different command.
- A scan or photo holds a slot for as long as the model reads it (12 to 40 s, longer while the model is cold: 85 s for
  the first 3-page scan), so a few at once can make library requests answer `CNV-429-BUSY-001`. A separate limit for
  reading is the fix if that happens.
- A PDF with text on some pages is still taken as digital (`is_scanned_pdf`, the same as on a job), so its scanned
  pages' text is lost. Reading page by page is the next change.
