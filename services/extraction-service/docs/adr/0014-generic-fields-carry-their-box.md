# 14. The generic read's fields carry the box where each value is printed

Status: Accepted · Date: 2026-11-13 · Deciders: Python team lead (Prakyath), with the frontend team

## Context

The frontend builds custom templates (ADR 0013) from documents it has never seen. It sends the document to the
generic read first, then lets the user turn the fields into a template. A template's keys are read fastest and most
reliably by box (OCR only, about 1 to 3 seconds instead of 15 to 25 for the generic read), so the frontend asked for
the place of each value. The model can be asked for coordinates, but a 4B model's boxes are loose and each one adds
output tokens to our slowest path.

## Decision

The model reads the values; OCR places them. While `generic-read` runs, OCR engine A reads the uploaded pages as they
are (not cut, split or turned) and returns every word with its box. Each text value is placed on the run of words that
spells it (letters and digits, case ignored); a value printed more than once (two dates) is placed next to its printed
label; a value OCR joined to its label ("Email:a@b.com") takes that word's box. A value that isn't found, or can't be
told apart from another place, gets no box. Tables get none.

Each field in the response gains `box: {page, x, y, w, h}`, percent of the uploaded page, page 1 or 2: the same form a
custom key's box is sent in, so the frontend can send it back unchanged. The other routes answer `box: null`.

## Consequences

- No added wait: OCR takes a second or two and runs while the model reads.
- A box is only as good as OCR: handwriting, a very blurred value or a value the model reworded gets `null`, never a
  guess.
- An added response field; existing callers are unaffected.
- Boxes match the picture as uploaded, as for custom keys: the frontend must draw on the same picture it sent.
