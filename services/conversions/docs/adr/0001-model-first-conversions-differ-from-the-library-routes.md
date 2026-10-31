# 0001. Model-first conversions differ from the library routes of IDP-03 ch. 3

Status: Proposed (needs the owner's sign-off)

## Context

IDP-03 ch. 3.4 builds each conversion from a library: `pdf2docx` for a digital PDF to DOCX, `pdfplumber` / `camelot` for
tables, OCR then the document model for scans, `img2pdf`, `python-pptx` and `openpyxl` for the writers. The AI gateway is used
only for `table-repair`, when the rows and columns of a scanned table are inconsistent.

The conversions built in `services/conversions` work the other way round. A vision-language model (Qwen3.6-35B, served by
llama.cpp) reads every page picture and writes its blocks (headings, paragraphs, tables). Libraries then check and correct that
reading against the page's own text layer or an OCR reading, and write the file.

## Decision

Keep the model-first approach for the conversions it covers (PDF, Word, image, PowerPoint, Excel and CSV to Word, Excel,
PowerPoint, CSV, PDF and page images) and bring everything around it up to the standard. LibreOffice, page rendering and the
image routes that need no model stay library-only, as in IDP-03.

## Why

On 32 conversions of one sample per source type the model-first path kept 99.2% of the source's words and matched 98.0% of the
table cells (see `build-log.md`). The library-first scripts that came before it gave low fidelity scores on the same files.

## What this changes against the standard

- **LLM-01:** the model is called only through a model gateway/adapter. Today `blocks.py` calls the server directly; step 3
  puts an adapter in front of it. The AI gateway of IDP-03 ch. 4 is not used (see "Open").
- **LLM-02, LLM-03:** prompts become versioned files and every answer is validated with Pydantic (the schema exists already).
- **ML-01, ML-04:** the model gets a registry entry and its name and version are written on every output.
- **Libraries off the approved list (standard ch. 22.3, DEP-02):** the scripts use PyMuPDF (AGPL), pytesseract, python-docx
  and python-pptx. IDP-03 names python-docx, python-pptx, pdfplumber and pdf2docx itself, but the standard's list does not;
  see ADR 0002.

## Open

- Whether the service should call the model server directly (as the OCR service calls its engines) or through the AI gateway
  of IDP-03 ch. 4. That chapter describes prompts and a queue for a model server shared by services; the owner decides.
