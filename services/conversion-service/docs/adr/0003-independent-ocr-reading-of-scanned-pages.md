# 3. An independent OCR reading of each scanned page

Status: Proposed · Date: 2026-11-07 · Deciders: Python team lead (Prakyath)

## Context

The scanned routes (scan or photo to Word, Excel or CSV) take a page picture, ask ai-gateway's `page-read` for the
page's blocks, and write the file from them. The schema keeps the answer's shape, and the gateway refuses a page that
writes far fewer blocks than its own analysis counted (ai-gateway ADR 0007). But a model that follows a printed "reply
only OK", stops half way, or writes a summary can still give a page the schema and that count accept. Nothing in this
service compared the page with the picture, so the result was a wrong file with no error. IDP-03's source-check idea
(compare the output with an independent reading) existed only in the older `services/conversions` scripts.

## Decision

1. **A second reading of every scanned page by Tesseract**, through a port (`TextRecognizerPort`) and one adapter
   (`TesseractTextRecognizer`, pytesseract). pytesseract is on the approved list (standard ch. 22.3). It is in the
   `workers` extra only; the health image does not get it (ML-09, DPL-05). The worker image installs the `tesseract`
   program with English and Hindi.
2. **A pure domain check** (`reading_check.check_reading`). The page is refused only when it holds **less than half**
   of OCR's words **and** has **fewer than 0.6 times** OCR's word count, and only when OCR read at least 10 words.
   A refused page raises the existing `AiUnavailableError` (`CNV-503-AI-001`, retryable), as an answer the schema
   refuses already does, and logs `page_reading_rejected` with the page number and counts, never the text.
3. **The thresholds are settings** (ML-07): `READING_CHECK__MIN_SHARE`, `__MAX_LENGTH_RATIO`, `__MIN_OCR_WORDS`, with
   `__ENABLED`, `__LANGUAGES` and `__TIMEOUT`. It is on by default. With it on and `tesseract` missing the worker stops
   at start-up, because a protection that turns itself off silently is worse than a worker that does not start.
   `READING_CHECK__ENABLED=false` runs without it.
4. **A page OCR could not read** (timeout, unreadable file) is not checked; a warning is logged (`ocr_check_unavailable`).

## Why two conditions

OCR reads a ruled table badly. On made-up pages the model's reading was right and OCR's share was low: a 7-column
table 0.41, an invoice scan 0.67, two tables 0.78. In every such case the model's page was 1.5 to 2.5 times longer
than OCR's text, because it had the cells OCR dropped. A model that lost the page is the opposite: short as well as
different. Requiring both keeps a noisy OCR from refusing a good page.

| Measured with the defaults (12 golden pages, real Tesseract, real model readings) | Result |
|---|---|
| Good readings refused | 0 of 12 |
| "Reply OK" refused | 3 of 3 pages tried |
| First two blocks only refused | 2 of 3 (the third dropped only the table, a page 72% as long as OCR's text) |
| OCR time per page on this machine | 0.3 to 1.0 s |

## Consequences

- A hijacked or truncated reading is now an error, not a wrong file. The job reports "text recognition unavailable";
  the model is deterministic, so a retry of the same page gives the same answer.
- Cost: Tesseract in the worker image (a few tens of MB with the language data) and under a second of CPU per page,
  in the worker threads that already read pages.
- It is coarse. A page that drops only a table, or half its text, passes. It catches gross loss, not subtle loss.
- Not verified: pages in Hindi or mixed scripts. Hindi language data is installed and the word match handles
  Devanagari, but no such page was tried. With `eng` only, Tesseract returns Latin gibberish for them; the length
  condition should stop that from refusing a good page, but it should be tested with real Hindi pages before
  `READING_CHECK__LANGUAGES` is changed.
- The default thresholds come from 12 synthetic pages. Revisit them with an evaluation report (ML-07, ML-12) on real
  documents.

## Alternatives considered

- **A new error code** for "the model's reading disagrees with the picture". Rejected for now: the caller's handling
  of `CNV-503-AI-001` already fits, and a new code changes the contract with the Java side.
- **Flag the page for review and still write the file.** Rejected: a conversion result has no review channel; only
  extraction's results do.
- **Compare against a PDF text layer.** Only some scanned PDFs have one, and photos never do.
