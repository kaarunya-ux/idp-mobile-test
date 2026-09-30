# Field-canvas test — MiniCPM-V 4.6, PAN front, on-device (2026-09-30)

Device: Samsung Galaxy M35 (SM-M356B), CPU only, 4 threads pinned to cores 4-7, `-c 4096 --no-warmup`,
`pan_full` prompt + grammar, greedy decoding (`--temp 0`).
Method: the four field regions (PAN number, name, father's name, DOB) were cut from each card image,
stacked into one 448x448 canvas, and sent to the model as a single image (1 vision tile).
Full write-up: `../MINICPM_LATENCY_FINDINGS.md`.

## What was run

5 distinct cards x 5 repeats = **25 runs**, run through the adb test harness (`run.sh` on the phone).
Repeats are identical by design (greedy decoding), so this is **5 distinct results** repeated for timing.

## Result

| Card | Avg time | Fields right | Failure (same in all 5 runs) |
|---|---|---|---|
| a  ROSHAN SAHANI | 22.1 s | 5/5 | – |
| b  SAMAD | 22.2 s | 5/5 | – |
| c  ARJUN MANDLOI | 22.8 s | 4/5 | `pan_number`: `FCLPM0017F`, printed `FCLPM0001F` |
| d  ANIZ ISLAM | 22.5 s | 5/5 | – |
| e  SAROJINI M | 22.6 s | 4/5 | `name`: `SAROJIN M`, printed `SAROJINI M` |
| **All** | **22.4 s** (min 18.9, max 23.8) | **115/125 = 92%** | 10 wrong fields = 2 errors x 5 runs |

Documents fully correct: **15/25 = 60%** (3 of 5 cards).
Same 4 cards (a-d) sent as a whole photo at 560 px (current app path, simulated camera frame, `-c 2048`):
avg 42.5 s, 10/20 fields (50%), 1/4 documents fully correct.

In-app check (the switch added to the app): SAMAD 21.8 s and UDAYRAJ SINGH 22.2 s, both 5/5.
Those two are in the app's `run_log.txt` on the phone.

## Files

- `runs.csv` — every run: card, run number, seconds, ground truth vs model output for each field, wrong fields
- `raw_logs/` — the 25 raw llama.cpp CLI logs (`out_rep_<card>_<run>.log`)
- `harness_output.txt` — the harness's own per-run output
- `canvas_images/` — the exact 448x448 image the model received for each card
- `source_cards/` — the card image each canvas was cut from

## Read this before quoting the numbers

- **Ground truth was read by eye** from the card images. No verified answer key exists.
- **Best-case inputs.** Four of the five cards were already tight front-card crops, the fifth a clean
  front-card crop from a screenshot. No live camera photos. Real photos need the card found and
  straightened first, which is not built.
- **The bands were widened after the first attempt** (tight boxes clipped fields on two cards), so the
  92% is partly fitted to these five cards.
- **Tiny sample:** with 5 cards, the 95% range on 60% (documents) is roughly 23-88%, and on 92%
  (fields) roughly 75-98%.
- **The PAN number, the field that matters most, was 4/5.** The wrong one (`0017` vs `0001`) looks
  valid; PAN has no checksum to catch it.
