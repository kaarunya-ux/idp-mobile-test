# 13. Custom templates: the keys come with each request; we store nothing

Status: Accepted · Date: 2026-11-04, amended 2026-11-07 (boxes) · Deciders: Python team lead (Prakyath), with the
backend team

## Context

Users will define their own templates for documents we have no standard template for (a green card, say): a list of
keys with the label printed for each. The backend keeps those definitions. The question was how extraction gets them.
Two options were on the table: Python reads them from the backend's database (a shared database), or the backend sends
them with each request.

## Decision

1. **The backend sends the keys with each request** (`POST /api/v1/extract/custom`, multipart `file`, optional `back`,
   and `fields`: a JSON list of `{key, label, type, box}`, type `text`, `date` or `number`, 1 to 50 keys). We store
   nothing; no shared database (DB-01: never read another service's tables).
2. **OCR first, inside the box when there is one.** `box` (optional, added 2026-11-07) is the rectangle the user drew
   around the value on the sample, as the template designer stores it: `{page, x, y, w, h}` in percent of the page,
   top-left corner and size; `page` is 1, as fields are read on the first page. A key with a box is read from the
   words inside it, widened by 0.5% of the page left and right and 1% above and below; a piece of text crossing the
   box's edge keeps only its words inside, so the label before the value and the next column after it are left out.
3. **By label, when there is no box or the box holds nothing** (the document printed elsewhere than the sample): the
   field is found by its label where the label opens a piece of text, and its value is the text after the label on
   its line up to the first column gap (4% of the page's width), or the line below. When the label isn't on the page,
   OCR reads nothing for it (it never guesses from the rest of the page). Both engines must agree, as for any field;
   dates and numbers are checked as such.
4. **The AI for what OCR leaves unsure**, in one call, with the labels; the usual limit of ten unsure fields (a sign of
   a bad photo on a standard card) does not apply, since an unprinted label is normal on a custom document.
5. **Standard documents stay ours** (PAN, Aadhaar, DL: our templates in our database, identified by their keywords):
   the backend sends only the file.

## Consequences

- No template storage or sync for custom documents on our side; the backend owns them.
- A document whose labels are printed is read by OCR alone (a made-up card: four keys in 1.7 s); each unprinted or
  unusual label costs the AI call (about 5 s on the Mac mini's model, one call for all of them).
- A date is accepted in any printed form and returned as printed (amended 2026-11-11, below).
- A label printed differently from what the user typed ("USCIS#" for "Card number") is not found by OCR; the AI reads
  it, and may pick the wrong value (a made-up invoice: the buyer's phone for "Seller phone"). A box avoids both: that
  key was read right by OCR alone.
- Until 2026-11-07 the value was the rest of the label's line, so on a two-column document it ran into the next column
  ("GSTIN 32A... Transport ID 24B..."), at 99% confidence: both engines read the same wrong text. The column gap and
  the box fix that; confidence still says the engines agree, not that the value is the right one.
- Not yet: lining a page up with the sample by its printed words (the designer's `fixedWords`), which would need
  where those words sit on the sample, and per-key regex rules. A box on a document printed shifted from the sample
  falls back to the label.

## Amendment 2026-11-11: dates as printed

A custom `date` key only accepted numbers with the day first. A date printed with a month name (`20 OCT 2002`) or the
month first (`10/26/32`) was not found by OCR, and the AI's answer, copied as printed, was refused by the same rule, so
the key came back empty on every such document. A custom value is returned as printed, never reformatted, so the rule
now asks only whether the value is a date: numbers with the day, the month or the year first, two- or four-digit years,
and month names (`rule/printed_date.py`, validator `DATE_AS_PRINTED`). Numbers pass when one reading of them is a real
calendar date; `31 FEB 2002` and `13/13/2002` still fail. The standard cards keep their strict date rules.

A label typed with closing punctuation (`USCIS#`, `No.`) now takes that punctuation as part of the label, so `#` is no
longer returned as the value.

## Amendment 2026-11-11: boxes are on the picture as uploaded

A box is measured on the picture the user drew it on: the upload itself, not a page the service cut out of it. A
request with at least one box is therefore read on the upload as it is, cleaned but never cut to the card, split into
front and back, straightened or turned. Requests with labels only keep those steps, which help photos. A user who
draws boxes on a photo taken at an angle gets the photo as it is; a box drawn on a tilted scan may need a slightly
larger box.

## Amendment 2026-11-12: each key may carry the caller's id

The backend gives each key an `id` of its own and maps the answer back by that id, not by the key name a user typed.
A field in `fields` may carry `id` (optional, 1 to 128 characters, each id once per request); the response's field for
that key carries the same `id`. Fields sent without one, and the other routes, answer `"id": null`. The id is only
echoed: it is never read, stored or used to find the value, so the extraction is unchanged. Adding a response field
is backwards compatible; a caller that sends the same id twice gets 400 `EXT-400-VALIDATION-001`.
