# 1. OCR-first reading of scanned pages

Status: Proposed (behind a switch, default off) · Date: 2026-11-05 · Deciders: Python team lead (Prakyath)

## Context

The scanned routes (scan or photo to Word, Excel or CSV) read each page with a vision model through ai-gateway's
`page-read` (model-first, Sumukh's approach): the Qwen3.6-35B looks at the page and writes every word of it, with the
layout. On a 3-page scan, about 15 s a page, nearly all of it the model writing about 400 tokens at about 30 tokens a
second; four pages at once, 25 s for the three. The text is the model's, so it can misspell or invent words, and
nothing on our side can check it.

Extraction already runs PP-OCR on the CPU (`idp_ocr_engines`, engine A: PP-OCRv6 small), at 1 to 2 s a page.

## Decision

1. **OCR reads the text, the model only lays it out.** A new reader (`OcrLayoutPageReader`) runs PP-OCR on the page
   and numbers every piece of text it read (a line, or a table cell). ai-gateway's new `page-layout` task (ai-gateway
   ADR 0006) gets the page picture and the numbered lines, and answers the blocks (heading, paragraph, list, caption,
   table) as line numbers only. `domain/page_layout.py` rebuilds the page from OCR's text: the document's words are
   always OCR's.
2. **Nothing fails because a part is missing.** A line the model leaves out is kept, as a paragraph where it sits.
   When ai-gateway can't answer (down, daily cap, an answer that doesn't fit), OCR's own blocks become paragraphs.
   When OCR can't run (its models missing), the page goes to the model-first reader as before.
3. **A switch, `AI_GATEWAY__PAGE_READER = model | ocr_layout`, default `model`.** Nothing changes until the two are
   compared on real scans and the team picks one.
4. **A copy of extraction's OCR library.** `src/idp_ocr_engines` is extraction-service's, unchanged (checked with
   `diff -rq`), because each service builds its image from its own folder. An import contract keeps it free of
   service code. It moves to the shared `idp-py-platform` package when that exists; until then a change to it is made
   in both copies.
5. **New dependencies, in the `workers` extra only:** `numpy`, `onnxruntime`, `opencv-python-headless`, the same as
   extraction-service's and on the approved list (standard ch. 22.3, "CV / OCR / ML"). The health image doesn't get
   them (import contract). The worker image gains `libglib2.0-0` for OpenCV and `models.yaml`.
6. **The models are not in the image.** `models.yaml` pins extraction's PP-OCRv6 small detector and recogniser by
   checksum (ML-01); the files are mounted at `OCR__MODELS_DIR`. They load on a worker's first scanned page, not at
   startup, so workers that never read scans never hold them.

## Consequences

- Measured on the 3-page scan (`docs/conversion_docs/input/input.pdf`), without page-layout (the fallback): 5.5 s for
  the three pages, model loading included, against 25.2 s model-first; 210 words against the model's 212, but the
  three tables come out as paragraphs. Tables are what page-layout is for: that run is the next measurement.
- A page with more than 2000 pieces of text skips the model and keeps OCR's paragraphs (ai-gateway's limit).
- English only, as the sync lane. Hindi would need engine B's Devanagari recogniser.
- Two copies of `idp_ocr_engines` until `idp-py-platform` exists.
