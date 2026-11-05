# 13. Custom templates: the keys come with each request; we store nothing

Status: Accepted · Date: 2026-11-04 · Deciders: Python team lead (Prakyath), with the backend team

## Context

Users will define their own templates for documents we have no standard template for (a green card, say): a list of
keys with the label printed for each. The backend keeps those definitions. The question was how extraction gets them.
Two options were on the table: Python reads them from the backend's database (a shared database), or the backend sends
them with each request.

## Decision

1. **The backend sends the keys with each request** (`POST /api/v1/extract/custom`, multipart `file`, optional `back`,
   and `fields`: a JSON list of `{key, label, type}`, type `text`, `date` or `number`, 1 to 50 keys). We store nothing;
   no shared database (DB-01: never read another service's tables).
2. **OCR first, by label.** A template is built from the keys for that request: each field is found by its label
   where the label opens a piece of text, and its value is the text after the label on its line, or the line below.
   When the label isn't on the page, OCR reads nothing for it (it never guesses from the rest of the page). Both
   engines must agree, as for any field; dates and numbers are checked as such.
3. **The AI for what OCR leaves unsure**, in one call, with the labels; the usual limit of ten unsure fields (a sign of
   a bad photo on a standard card) does not apply, since an unprinted label is normal on a custom document.
4. **Standard documents stay ours** (PAN, Aadhaar, DL: our templates in our database, identified by their keywords):
   the backend sends only the file.

## Consequences

- No template storage or sync for custom documents on our side; the backend owns them.
- A document whose labels are printed is read by OCR alone (a made-up card: four keys in 1.7 s); each unprinted or
  unusual label costs the AI call (about 5 s on the Mac mini's model, one call for all of them).
- Dates are checked in numeric formats (dd/MM/yyyy, dd-MM-yyyy, dd.MM.yyyy, yyyy-MM-dd); a date printed with a month
  name goes to the AI.
- A label printed differently from what the user typed ("USCIS#" for "Card number") is not found by OCR; the AI reads
  it. Aliases per key would be the next step, if needed.
