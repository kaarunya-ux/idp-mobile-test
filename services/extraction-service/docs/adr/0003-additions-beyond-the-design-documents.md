# 3. Additions that go beyond IDP-03 and the Python Engineering Standard

Status: Proposed (awaiting the Python lead)

## Context

IDP-03 and the Python Engineering Standard are the ground truth for this service. While building and testing on the
sample PAN and Aadhaar cards, the owner asked for the features below. The documents do not mention them. Each is kept
on the owner's instruction and is listed here so a reviewer can accept it, change it or ask for it to be removed.

## Decision

Keep the following. "Document says" quotes or summarises what IDP-03 / the standard has on the point.

| # | Addition | Document says | Why it is kept |
|---|---|---|---|
| 1 | Extra template-field keys: `extract`, `labels`, `keep`, `above`, `max_lines_above`, `excludes`, `starts_at`, `language` (`pipeline/domain/template.py`) | IDP-03 ch. 2.5 fixes the keys of definition v1 and says the API rejects any definition that fails `idp-contracts/schemas/template-definition.v1.json` | Cards print the same facts in two scripts and layouts vary; fixed page regions read wrong values on tall and wide scans |
| 2 | Hindi fields `name_hi`, `address_hi`; a field is read only by engines that read its language; B and C must agree | Silent on Hindi. Acceptance is "A = B" (ch. 2.6, stage 6) | Hindi is printed on the cards and was read by the prototype |
| 3 | Aadhaar `address` and `pincode` fields | The example template (ch. 2.5) shows only PAN fields | The prototype and the frontend contract include them |
| 4 | Address similarity uses the 0.95 name default | Ch. 2.7 gives `VERIFY__NAME_SIMILARITY` 0.95 as the default for `SIMILARITY` fields only | No other number exists; no evaluation report yet (ML-07, ML-12) |
| 5 | Aspect ratio lowers CLASSIFY confidence instead of refusing | Ch. 2.6 stage 4 lists aspect ratio as an input without a rule | Cropped Aadhaar scans are about 3:1 |
| 6 | ~~Indra OCR as slot C (optional extra)~~ **Withdrawn 2026-10-26** | Standard ch. 22.3 allows "engine libraries approved per project by ADR"; ADR 0002 covers the licence | Parked: the licence rules it out (ADR 0002). Slot C is empty until another engine is chosen |
| 7 | Readers for `models.yaml` and `inference.yml` with no YAML library | ML-01 names `models.yaml`, no parser is on the approved list | See ADR 0001 |
| 8 | `scripts/try_extract.py` | Standard ch. 22.2 lists the Makefile targets and no scripts | Manual check on sample cards |
| 9 | PP-LCNet document-orientation model | IDP-03 stage 3 names no model | Turns sideways and upside-down scans upright |
| 10 | Engines A and B read in two threads of one process, not as two Celery tasks | Ch. 2.6 uses a Celery `chord(group(ocr_a, ocr_b), compare)` | The Celery layer (step 6) is not built; threads give the same parallelism |
| 11 | Engine C reads the whole page once, only when a field is unresolved, not the field crop | Ch. 2.6 stage 7: "Run OCR C on the field crop only" | Measured: Indra's crop reads gave junk (`H I`, `I I I`) on Hindi names that it read correctly on the page, even with a margin of 4 line heights |

## Consequences

Good: the service reads the sample cards in both scripts. Bad: items 1 to 5 mean a template stored under schema v1 can
not carry these rules until the schema in idp-contracts gains the keys (or the logic moves elsewhere), and Hindi
fields cannot be accepted without a second Hindi reader (only B reads Hindi now that Indra is parked). No evaluation report backs
items 2 to 5 yet.

## Alternatives considered

Remove each addition and keep only what the documents mention, which would leave Hindi, address and column-aware
reading out of the standard templates.
