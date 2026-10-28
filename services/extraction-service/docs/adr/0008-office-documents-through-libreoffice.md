# 8. Office documents are read by turning them into PDFs with LibreOffice

Status: Proposed · Date: 2026-10-28 · Deciders: Python team lead

## Context

IDP-03 ch. 2.6 stage 5 reads "Digital PDFs/Office: pdfplumber / python-docx / openpyxl text with boxes — no OCR".
Templates find fields by where they are on the page (boxes and anchors). A Word file's text has no positions until it
is laid out, and an Excel sheet's cells have no page coordinates, so python-docx or openpyxl text can't be matched to
a template's boxes without inventing a layout.

## Decision

The worker turns Word, Excel, PowerPoint and OpenDocument files (`doc`, `docx`, `odt`, `xls`, `xlsx`, `ods`, `ppt`,
`pptx`, `odp`, by content, FIL-03) into a PDF with LibreOffice, then reads that PDF like any digital PDF: exact text
with positions from its text layer, page images for reviewers, no OCR. It is the same LibreOffice call as
conversion-service's `office_to_pdf` (a throwaway profile per document, macros off, a hard timeout that kills the
whole process group). The design samples go the same way. `ExtractionRequested` now accepts the Office content types.

## Consequences

- No new Python dependency; LibreOffice is already on our list of permissive tools and in conversion-service's image.
- The worker image grows by about 500 MB (LibreOffice and the Noto fonts).
- Fields are found where LibreOffice puts them, so a template for Word or Excel documents is designed on samples of
  them (the designer renders samples the same way).
- A document LibreOffice can't open, or that takes longer than `OFFICE__TIMEOUT`, fails with `EXT-422-FILE-001`.
- Linked content in a document (remote images) is not fetched on purpose; the workers' network policy should block
  outbound traffic except to the platform's services.

## Alternatives

python-docx and openpyxl, as IDP-03 says: no positions, so no template matching (above), and python-docx would need its
own ADR (not on the approved list). Calling conversion-service: services don't call each other; Java orchestrates.
