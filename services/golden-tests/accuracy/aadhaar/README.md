# Aadhaar accuracy set (extraction)

100 invented Aadhaar cards (English only) sent to `POST /api/v1/extract`, every field scored: correct, wrong and ACCEPTED, wrong and NEEDS_REVIEW, or missing; also exact-match accuracy and the review rate. The committed
[BASELINE.md](BASELINE.md) is the result of the last run; [FINDINGS.md](FINDINGS.md) lists bugs seen while running it (they
are written down, not fixed here).

```bash
cd services/golden-tests
EVAL_EXTRACTION_URL=http://<extraction-sync host>:8003 make accuracy-aadhaar   # draws, sends, scores, rewrites BASELINE.md
make test-accuracy                                                              # scoring and card tests, no service
```

- `EVAL_EXTRACTION_URL` has no default. `EVAL_TOKEN`, if set, is sent as `Authorization: Bearer` and is never written to a file.
- Made-up data only: names use invented surnames, numbers are random with a Verhoeff check digit. The pictures are drawn at
  run time into git-ignored `cards/`, answers go to git-ignored `results/`. Nothing here is a real person or card.
- The set: 10 clean controls, 8 cards of each defect (photo at an angle, strong blur, faded print, glare, low resolution, a
  crop that cuts a field edge, the card on a table), 32 with two defects, and 2 cards whose number has a wrong check digit.
  Names and addresses use letters OCR confuses (rn/m, cl/d, l/1, O/0) and the states Karnataka, Haryana and Chandigarh.
- Scoring: **wrong and ACCEPTED** (nobody will check it) is reported apart from wrong and NEEDS_REVIEW; **exact** compares the
  text character for character, since values come back as printed; **review rate** is the share of fields marked NEEDS_REVIEW.
- The number comes back masked (`XXXX XXXX 1234`) and is correct only in exactly that form with the printed last four digits.
  For the wrong-check-digit cards the right outcome is the opposite: the number must not be accepted (NEEDS_REVIEW, still
  masked); they are reported as their own class, apart from the accuracy of the others.
- `card_set.py` (cards and values) · `draw.py` (layout and defects) · `score.py` (scoring) · `run.py` · `report.py`.
