# 0002. Libraries the conversions use that are not on the approved list

Status: Proposed (needs the owner's sign-off)

Standard ch. 22.3 lists the approved libraries; DEP-02 says a library outside it needs an ADR.

| Library | Used for | On the list? | Proposal |
|---|---|---|---|
| pypdfium2, Pillow, openpyxl, pikepdf, httpx, NumPy | page rendering, images, Excel, HTTP | yes | use |
| python-docx, python-pptx | writing Word and PowerPoint files | no (IDP-03 ch. 3.3 names both) | approve; no approved library writes these formats |
| pdfplumber | table detection in digital PDFs | no (IDP-03 ch. 3.4 names it) | approve |
| pytesseract and Tesseract | the OCR check of scans and the layout of scanned pages | no ("engine libraries approved per project by ADR") | approve here, or use `idp-ocr-engines` |
| PyMuPDF (`pymupdf`) | text layer with fonts and positions, table finder, page images in every digital-PDF step | no | **replace** (below) |

PyMuPDF is licensed AGPL-3.0, which is a problem for a service the company ships, and the standard does not list it. The
approved `pypdfium2` gives page text with positions and renders pages; `pdfplumber` gives the table finder. Replacing PyMuPDF is
the largest single change the standard asks of this service and is planned as its own step (see `build-log.md`).
