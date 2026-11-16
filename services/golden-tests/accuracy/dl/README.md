# Driving licence accuracy set (extraction)

100 invented driving licences (English only) sent to `POST /api/v1/extract`, every field scored: correct, wrong and ACCEPTED, wrong and
NEEDS_REVIEW, or missing; also exact-match accuracy and the review rate. [BASELINE.md](BASELINE.md) is the result of the last run;
[FINDINGS.md](FINDINGS.md) lists what the service did wrong (written down, not fixed here). Same design as the Aadhaar set.

```bash
cd services/golden-tests
EVAL_EXTRACTION_URL=http://<extraction-sync host>:8003 make accuracy-dl   # draws, sends, scores, rewrites BASELINE.md
make test-accuracy                                                        # the accuracy sets' own tests, no service
```

- `EVAL_EXTRACTION_URL` has no default. `EVAL_TOKEN`, if set, is sent as `Authorization: Bearer` and is never written to a file.
- Made-up data only. The pictures are drawn at run time into git-ignored `cards/`, answers go to git-ignored `results/`.
- Fields scored (the standard template `standard-driving-licence`): `license_number`, `name`, `address`. The number follows the
  rule `[A-Z]{2}-?[0-9]{2}(19|20)[0-9]{2}[0-9]{7}` and is compared as the service returns it (upper case, spaces removed). Cards also
  print the date of birth, issue and valid-till dates, blood group and vehicle classes, as a licence does; these are not scored.
- A licence has two sides. Half the cards (the odd ones) are one picture with the front on the left and the back on the right
  (`side_by_side`); half are two pictures, sent as `file` and `back` (`two_pictures`). The report shows the two apart.
- The set: 10 clean, 8 cards of each defect (angle, strong blur, faded, glare, low resolution, crop through a field edge, card on a
  table), 32 with two defects, and 2 cards whose serial is a digit short (right outcome: NEEDS_REVIEW or no value, reported apart).
  For a side-by-side card the defect is applied to the whole picture; for two pictures, to each picture. Addresses use ten places in
  seven states, with letters OCR confuses.
- `card_set.py` (cards and values) · `draw.py` (layout and defects) · `score.py` · `run.py` · `report.py`.
