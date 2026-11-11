# Aadhaar accuracy set (extraction)

30 invented Aadhaar cards sent to `POST /api/v1/extract`, every field scored: correct, wrong, empty or review. The committed
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
- The set: 6 plain, 4 serif, 4 monospace, 3 each of blur, slight rotation (2 to 3.5 degrees), glare and low resolution (400 px
  wide), 2 with two defects, and 2 cards whose number has a wrong check digit. English only: extraction reads English.
- The number comes back masked (`XXXX XXXX 1234`) and is correct only in exactly that form with the printed last four digits.
  For the wrong-check-digit cards the right outcome is the opposite: the number must not be accepted (NEEDS_REVIEW, still
  masked); they are reported as their own class, apart from the accuracy of the others.
- `card_set.py` (cards and values) · `draw.py` (layout and defects) · `score.py` (scoring) · `run.py` · `report.py`.
