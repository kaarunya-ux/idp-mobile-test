# 16. Auto extraction: find the document's template, ours or the user's, and extract with it

Status: Accepted · Date: 2026-11-13 · Deciders: Python team lead (Prakyath), with the backend team

## Context

In the generic section a user may upload a document we, or the user, have a template for. Read with its template it is
faster and checked by rules; read generically it is neither. The user's own templates live with the backend, and only
the backend knows which a user may use (RBAC), as for custom templates (ADR 0013). The frontend sends `type=auto` when
the user clicks Extract; nothing happens on upload alone. Other products classify first and extract second, with
keyword rules, then a model, and a confidence threshold below which nothing is forced into a template.

We first built a detect-only route and a later extract with the approved template id; the product chose one call
instead, started by the Extract click.

## Decision

`POST /api/v1/extract/auto` takes `file`, optional `back`, and `templates`: the templates the user may use, sent by
the backend as `{id, name, fields}` with the fields as on `/extract/custom`. The candidates are those and the published
templates the account may use from our database (standard and its own). OCR engine A reads the uploaded pages as they
are; each candidate is scored on each page by the share of its keywords found (a user's template: its labels; ours:
its keywords, halved for a page of the wrong shape). The best score of at least `QUICK__DETECT_MIN_CONFIDENCE` (0.85)
wins, the user's own template on a tie. Otherwise ai-gateway's `classify` is asked which of the best-scoring 50
candidates (its limit) the first page shows, each as a document type code (ours `AADHAAR_CARD`, a user's from its name,
`JAMAICA_NATIONAL_ID`, made unique), on the INTERACTIVE lane; its pick counts only at 0.85 or more. Only candidates with evidence on the page (at least
their own minimum of keywords found) are offered; with none, the AI is not asked: the 4B named a Jamaican ID an Aadhaar
card, 90% sure, when it had to pick among templates the page showed no sign of. The document is then read with the
winner, as `/extract` reads a standard template or `/extract/custom` a user's (with the field ids sent back); with
none, it is read the generic way. A winner that reads under `QUICK__DETECT_MIN_FILLED` (0.5) of its fields is not
that document after all (the AI named PAN for an unrelated page whose only sign was "GOVT", and the read came back
empty): the document is read the generic way instead, `template` null. The answer is the extract's answer plus `template` (`id`, `name`, `confidence`,
`detectedBy`), null for the generic read.

## Consequences

- One call. Matching costs one OCR pass (one to two seconds); the keyword checks are milliseconds even for 150
  templates (measured: 750 checks in 34 ms). The AI is asked only when the keywords fall short.
- The backend sends a user's templates with each auto call (150 templates is about 53 KB).
- A template is found by its keywords or labels only if they are printed; ours need keywords in their definition.
- Engine A reads the pages once for matching and again in extraction; reusing that reading is a later optimisation.
- The backend stays the only judge of which templates a user may use; nothing about users is stored here.
