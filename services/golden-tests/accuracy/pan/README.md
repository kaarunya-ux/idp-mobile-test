# PAN accuracy set (extraction)

100 invented PAN cards (English only) sent to `POST /api/v1/extract`, every field scored: correct, wrong and ACCEPTED, wrong and
NEEDS_REVIEW, or missing; also exact-match accuracy and the review rate. [BASELINE.md](BASELINE.md) is the result of the last run;
[FINDINGS.md](FINDINGS.md) lists what the service did wrong (written down, not fixed here). Same design as the Aadhaar set.

```bash
cd services/golden-tests
EVAL_EXTRACTION_URL=http://<extraction-sync host>:8003 make accuracy-pan   # draws, sends, scores, rewrites BASELINE.md
make test-accuracy                                                         # the accuracy sets' own tests, no service
```

- `EVAL_EXTRACTION_URL` has no default. `EVAL_TOKEN`, if set, is sent as `Authorization: Bearer` and is never written to a file.
- Made-up data only. The pictures are drawn at run time into git-ignored `cards/`, answers go to git-ignored `results/`.
- Fields scored (the standard template `standard-pan-card`): `pan_number`, `name`, `father_name`, `dob` (dd/MM/yyyy).
- The set: 10 clean, 8 cards of each defect (angle, strong blur, faded, glare, low resolution, crop through a field edge, card on a
  table), 32 with two defects, and 2 cards with an invalid number. Names and numbers use letters OCR confuses (rn/m, cl/d, l/1, O/0).
- The PAN rule enforces the shape `AAAHA9999A` only, with a holder type from `ABCFGHJLPT` in position four. The 2 invalid cards use a
  holder type outside that list; the right outcome is NEEDS_REVIEW (or no value). They are reported apart from the accuracy figures.
- The fields are laid out where the template's regions expect them (the date of birth sits in the lowest fifth of the card).
- `card_set.py` (cards and values) · `draw.py` (layout and defects) · `score.py` · `run.py` · `report.py`.
