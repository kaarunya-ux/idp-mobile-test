# 3. Target TXT: the plain text inside any file, by the libraries and OCR, never a model

Status: Accepted · Date: 2026-11-13 · Deciders: Python team lead (Prakyath)

## Context

Users want "just the text in this document": no layout, no new file format, no template. Every input we accept
either carries its text (Word, Excel, PowerPoint, CSV, a digital PDF) or is a picture of it (an image, a scanned
page). A model reading a page takes 30 to 75 s on the 35B; OCR takes one to two seconds and reads printed English well.

## Decision

`POST /api/v1/convert` takes `target=TXT`, outside the conversion matrix (like JSON and XML), and answers
`<name>.txt` (UTF-8), or one ZIP of them for several files. The text is read by the libraries already in the image:
Word paragraphs and table rows (cells by tab), each sheet's rows, each slide's text and tables, a CSV as it is, a PDF
page's text layer. Pictures and scanned pages are read by OCR engine A (extraction's PP-OCR, English), loaded on the
first picture. A PDF page whose text layer has fewer than `OCR__TEXT_OCR_BELOW_CHARS` (200) characters is read with
OCR too, and the reading with more text is kept: this catches a scan with a stray line of text, as a browser prints one.
Pages, slides and sheets are separated by a blank line. No model is called.

## Consequences

- Fast and free of AI calls: libraries instantly, OCR one to two seconds a page.
- Text only: no layout, fonts or positions. Handwriting, non-Latin scripts and very poor photos read badly.
- Synchronous only for now; a long scan (one to two seconds a page) should go as a job once jobs take TXT.
- A new value in the format list: the backend must send `TXT` for it.
