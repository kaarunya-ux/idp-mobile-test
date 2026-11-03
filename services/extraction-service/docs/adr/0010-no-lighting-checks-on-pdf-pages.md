# 10. No lighting checks on PDF pages, and a setting to note a photo's lighting instead of refusing it

Status: Proposed · Date: 2026-11-03 · Deciders: Python team lead

## Context

The quality gate (IDP-03 ch. 2.6 stage 2, ch. 2.7) refuses a page that is blurred, too dark, too bright, has glare,
or has too low a resolution. Brightness and glare describe the light a **photo** was taken in. The backend's first
synchronous tests sent clear PDFs of Aadhaar cards printed on A4, and every one was refused with
`EXT-422-QUALITY-003` ("glare"): the white paper around the card measured as 68% glare (the limit is 4%). The glare
measure already ignores saturated areas with sharp edges; on a rendered page the paper still fades softly into the
card's light background. The same model reads these documents perfectly, so the gate refused readable work.

## Decision

1. **A page rendered from a PDF (or from an Office file, which arrives as a PDF) is never judged on lighting**: no
   brightness or glare check. Sharpness and resolution are still checked. The scores are measured and kept in the
   result as before.
2. **`QUALITY__LIGHTING_REJECTS`** (default `true`, as IDP-03): `false` notes a photo's lighting in the result instead
   of refusing it, so OCR and the AI read on. The local integration stack sets `false`, so the backend's tests are not
   blocked by the gate while it is tuned.

## Consequences

- PDFs with real problems of light (a photo pasted into a PDF, with glare) are no longer refused for it; OCR and the
  AI then try, and the unsure fields go to review as usual.
- The default for photos is unchanged until the gate is evaluated on real photos (ML-07, ML-12): how many good photos
  the glare check refuses, and how many bad ones it catches.
