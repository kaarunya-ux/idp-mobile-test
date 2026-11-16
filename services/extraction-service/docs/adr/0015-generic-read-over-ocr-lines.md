# 15. The generic read can run over OCR's lines: the model names lines, OCR supplies the text

Status: Proposed (behind `QUICK__GENERIC_MODE`, default `picture`) · Date: 2026-11-13 · Deciders: Python team lead
(Prakyath)

## Context

The generic read is our slowest path: the model reads the page image and writes every key and value as JSON, about
60 tokens a field, and the model server writes about 30 tokens a second (a card 15 to 35 s; an invoice runs past the
40 s timeout). Measured on one card (4B, `10.0.3.99`): reading the picture 32.8 s; giving the model only OCR's text
34.4 s (it still writes every value); giving it OCR's numbered lines and asking only for line numbers 19.7 s. The time
is in what the model writes, not in what it reads.

## Decision

`QUICK__GENERIC_MODE=lines`: OCR engine A reads the uploaded pages as they are (one to two seconds), the lines are
numbered across the pages, and ai-gateway's new `generic-lines` task (text model, no image) answers key, label, type
and line numbers per field, never text; a table is rows of cells of line numbers. Extraction fills each value with
OCR's words on those lines, leaving out a label printed on the same line ("DL No: TS0100", "Email:a@b.c"), with the box
of those words (ADR 0014) and OCR's mean confidence. A document OCR finds no text on is read as a picture. `picture`
(the default) is unchanged.

## Consequences

- Faster by what the model no longer writes; values are OCR's exact characters with exact boxes.
- What OCR misreads or misses (handwriting, stylised text, a faint stamp) is wrong or missing, where the picture way
  can still read it. Hindi and other scripts are read only as far as the OCR engine's languages go.
- Stays Proposed until both modes are measured on the accuracy sets and real documents for speed and correctness;
  then the default is the owner's decision.
