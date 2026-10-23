# On-device latency and accuracy: findings of 2026-10-22 and 2026-10-23

Phone: Samsung Galaxy M35 (SM-M356B, Exynos 1380, 5.5 GB RAM, Android 16). Models: MiniCPM-V 4.6 (about 1B class), Qwen3-VL 2B, LFM2-VL 3B, all Q4_K_M with Q8 image encoders. Native libraries: the "dist_dot" build from the pinned script unless a row says "old libraries". Nothing in the app code was changed to get these numbers. Branch `mobile_standards`, uncommitted.

## 1. Summary

1. **Latency is mostly the number of tokens the model has to read.** An image costs about one token per 1,024 pixels; the prompt text costs one token per 3 to 4 characters. On this phone the models read 10 to 19 tokens per second and write 8 to 14. So 800 tokens of image or prompt is 55 to 80 s before the answer starts.
2. **Resizing the input fixes speed.** At a 560 px longest edge, every model finishes DL, passport and Aadhaar in 14 to 34 s. At full size, Qwen and LFM2 take 60 to 90 s on large images, and LFM2 can take more than 170 s.
3. **Resizing costs accuracy on small print.** Qwen on passport: 33/34 fields at full size, 28/34 at 560 px.
4. **The pinned llama.cpp build tiles LFM2 images** (upstream PR #27057): a PAN card becomes 15 tiles and about 3,800 tokens, so LFM2 PAN takes 85 s or more at the app's size. A 500 px input avoids it. Qwen3-VL and MiniCPM-V are not affected.
5. **LFM2 needs free memory.** Its files are 2.15 GB on disk and the server held about 2.2 to 2.7 GB. With about 1 GB free, the phone thrashes and requests take 100 s or fail.
6. **A shorter prompt and a small output grammar cut DL latency by 25 to 40%.**
7. **No model is clean across all four documents, and none reaches the 6 s target in IDP-04** (best: 14 to 22 s).

## 2. How it was measured

- The app's `llama-server` and `llama-mtmd-cli` flags were reproduced from `LlamaLaunch` (4 threads pinned to cores 4 to 7, flash attention on, CPU only, `-c 4096`, no warm-up), run from the command line over `adb`. The Android app itself was not used for these runs.
- Inputs: PAN uses the app's own prompts, grammars and resizes per model. DL, passport and Aadhaar use a compact prompt (4 to 7 lines, no system prompt) and a small grammar that fixes the JSON, so they drop fields the app's schemas require: DL keeps licence number, name, DOB, issue date, NT validity; passport keeps number, name, DOB, gender, place of birth, issue and expiry dates; Aadhaar keeps number, name, DOB, gender.
- Test cards: 5 PAN, 5 DL, 5 passport, 6 Aadhaar, kept in `data/india_docs/` (git-ignored). Expected values were read from the images by hand (`truth.md` in each folder) and need the owner's check. Passport card 5's issue year is hidden by glare and is not scored.
- Scoring is strict per field (case-insensitive, names compared as sets of words).
- The clean runs used a warm server, one request at a time, no early kills, an idle wait between requests, a thermal check before each request, and one retry on an empty reply. Runs killed early, runs with a lost connection, and runs made while the phone was out of memory are excluded.

## 3. Results

"Time" is per request. "Warm" is the warm server, "one-shot" reloads the model each time. Fields are correct fields against all fields scored.

**PAN (25 fields)**

| Model | Input | Time | Fields |
|---|---|---|---|
| Qwen3-VL 2B | 480 px (the app's path) | 19 to 20 s one-shot | **25/25** |
| LFM2-VL 3B | 500 px (the app's path since 2026-10-23; before it, 1,280 px, which left the card unshrunk) | 22 to 26 s one-shot, 16 to 18 s warm | 24/25 |
| LFM2-VL 3B | full size, new libraries | over 170 s (tiled, about 1,730 tokens; phone reached thermal 3) | 24/25 |
| LFM2-VL 3B | full size, old libraries | 24 to 30 s one-shot | 23/25 |
| MiniCPM-V | 448 px field canvas (the app's path) | 17 to 18 s one-shot, 11 to 14 s warm | 22/25 |
| MiniCPM-V | full card, about 314 tokens | 20 to 26 s warm | 20/25 |

**Driving licence (5 fields per card)**

| Model | Input | Time | Fields |
|---|---|---|---|
| MiniCPM-V | 560 px | 23 to 25 s | 20/25 |
| MiniCPM-V | full size | 23, 37, 24, 64, 26 s | 21/25 |
| Qwen3-VL | full size | 29 to 46 s on the 3 cards that finished in time | **15/15** |
| LFM2-VL | 560 px | 23 to 29 s (3 cards) | 13/15 |
| LFM2-VL | full size | 25 to 33 s on small cards, 81 s on card 2, over 170 s on the 2743x1904 photo | 19/25 |

**Passport (7 fields per card)**

| Model | Input | Time | Fields |
|---|---|---|---|
| Qwen3-VL | full size | 63 to 74 s on 4 large photos, 26 s on the small one | **33/34** |
| Qwen3-VL | 560 px | 26 to 34 s | 28/34 |
| LFM2-VL | 560 px | 25 to 29 s | 30/34 |
| LFM2-VL | full size | 86 s on card 1; later cards failed with the phone out of memory | not usable |
| MiniCPM-V | 560 px | 26 to 28 s | 25/34 |
| MiniCPM-V | full size | 40 s (card 1 only) | 6/7 |

**Aadhaar front (4 fields per card, 6 cards)**

| Model | Input | Time | Fields |
|---|---|---|---|
| MiniCPM-V | 560 px | 18 to 22 s | **22/24** |
| Qwen3-VL | full size | 21 to 29 s on small images, 42, 68 and 87 s on the three tallest | **22/24** |
| LFM2-VL | 560 px | 14 to 22 s | 19/24 |
| Others | | not run | |

Not run: Qwen at 560 px on DL and Aadhaar, Qwen PAN at full size, MiniCPM at full size on Aadhaar and most of passport, LFM2 at full size on Aadhaar.

**What the errors look like.** Qwen mostly misspells or shortens names (it drops a passport surname, writes `Megha Kumari`, `Jasgot Singh`); at 560 px it also misreads dates and a place of birth. LFM2 misreads whole names (`Sapna Rajya` for `SAPNA BAJAJ`) and two dates of birth. MiniCPM drops or swaps digits in licence and PAN numbers and merges place of birth into the passport name.

## 4. Why: tokens, speed and memory

| Cause | Evidence |
|---|---|
| Image tokens | LFM2 DL prompt 260 tokens at 560 px against 407 at full size (23 s against 33 s); Kerala card about 800 tokens (81 s) against 313 (29 s). Qwen passport 774 to 847 tokens at full size (52 to 63 s of reading) against 240 at 560 px (14 to 21 s) |
| Prompt text | The full DL prompt plus system prompt is about 900 tokens. In the first server test LFM2 needed 61 s to read 1,214 tokens and was killed before finishing. The compact prompt (about 60 tokens) took Qwen on DL from over 50 s to 29 to 36 s and MiniCPM from 33 to 43 s to 26 to 30 s |
| Token speeds | Reading: LFM2 10 to 15, Qwen 12 to 19, MiniCPM 14 to 15 tokens per second. Writing: LFM2 8 to 9, Qwen 10, MiniCPM 13 to 14. Steady across cool and warm runs, so heat is not the main driver |
| Tiling bug | `libmtmd` in the pinned build tiles when the rounded image area exceeds twice the cap's pixel budget (cap 128 gives 262,000 px; the PAN card is 468,000 px). The old libraries never tiled (see `STANDARDIZATION_HANDOFF.md` section 5) |
| Memory | LFM2 server about 2.2 to 2.7 GB. With 458 MB free: kswapd0 at 95%, memory stall 54%, 1.1 GB of the model resident, requests over 100 s. With 3 GB free: 23 to 29 s. Disk sizes with encoder: MiniCPM 1.26 GB, Qwen 1.55 GB, LFM2 2.15 GB |
| Warm server | LFM2 PAN 16 to 18 s against 26 to 29 s one-shot. The cache reuses only an identical request (187 of 191 tokens, 8 s); for a new card it reuses 1 token, because the image comes first |

## 5. Proposed code changes (not made; each needs the owner's and the team lead's approval)

1. **LFM2: shrink the input image. DONE in the working tree on 2026-10-23 (not committed), ADR 0007.** Before the change `downscaleForLfm2` capped the longest edge at 1,280 px, so an 860 px PAN card was not shrunk, and `ExtractionPlan.imagePrepFor` sent LFM2 the original image for Aadhaar, DL and passport (`ImagePrep.NONE`). Now `ModelProfiles` holds `LFM2_PAN_MAX_EDGE = 500` and `LFM2_DOCUMENT_MAX_EDGE = 560`, `ExtractionPlan.Plan.maxEdge` carries the edge, and `MainActivity.downscaleForLfm2(imageFile, longestEdge)` uses it. Tests: 231 pass; three planted bugs fail the new tests; `check.sh` is green. Measured at these sizes: PAN 22 to 26 s (24/25), DL 23 to 29 s (13/15), passport 25 to 29 s (30/34), Aadhaar 14 to 22 s (19/24). **Not yet done:** an in-app run on a phone (the numbers use `sips`-resized images from the command line), and the accuracy cost for LFM2 against full size.
2. **Compact prompts and grammars for DL, passport and Aadhaar** as new versioned files (`user_dl.v2.md` and so on, plus grammars). This changes which fields come back, so it is a product decision as well as an engineering one (LLM-02, ML-07, ADR 0005). It needs the evaluation set below first.
3. **Qwen on passport and Aadhaar: choose between full size (33/34, 60 to 87 s on large images) and 560 px (28/34, 26 to 34 s).** Not decided here.
4. **Lower image-token caps (256, 512) for non-PAN documents.** Not tested. The cap is a ceiling, and most of these images are under 1,024 tokens, so the saving is small (about 4 s per card at 256) and the risk is small print.
5. **Not code:** the build decision (options A, B, C in the handoff, section 5) and which models ship on which RAM tier.

Order: item 1 is done and waits for approval and an in-app phone check; item 2 needs an evaluation first.

## 6. Limits and open questions

- Five or six cards per document, one run per cell, command line only, no camera photos. Several cells are blank (see section 3).
- Truth values are the author's reading of the images.
- The 4B model on the other phone has not been tested: file names, libraries and RAM of that phone are still needed.
- No evaluation set, no CI, and the 21 phone checks are still open (`PHONE_CHECKLIST.md`).
- Raw logs and request files are in a temporary scratchpad and are not kept in the repository; this page holds the numbers.

## 7. Notes for anyone repeating these tests

- Never kill a request at a time limit on a single-slot `llama-server`: it keeps working for up to 40 s and the next request waits behind it. Let requests finish and flag the slow ones.
- Re-create the `adb forward` before every request, wait for `/slots` to report idle, and check the phone is cool (thermal 0 or 1) before each one.
- The phone's `nc` closes the connection on end of input, which cancels the request; use `curl` on the laptop through a forward.
- Clear background apps (`am kill-all`) and check free memory (above 2.4 GB) before starting a session. A long run heats the phone to thermal 3.
- Delete test files from `/data/local/tmp` and stop the server when done.
