# 9. Shrink the images sent to Qwen3-VL for DL, passport and Aadhaar to 560 px

Status: Proposed · Date: 2026-10-26 · Deciders: mobile extraction owner and team lead (to approve)

## Context

The app sent Qwen3-VL these documents at full size: `downscaleForQwen` only shrinks an image that holds more than twice Qwen's pixel budget (2.1 million pixels at a cap of 1024), and the test cards are smaller than that. Qwen reads about 12 tokens per second on the Galaxy M35, so a large image costs a lot before the answer starts:

- In the app, passport `pp_1` (768x1024): 847 prompt tokens, 50.3 s to read, **62.1 s** in total (Run 19).
- In the app, a licence (768x979): 804 prompt tokens, 61.5 s to read, **74.8 s** in total (Run 18).
- A real phone photo is 3,000 to 4,000 px wide; shrunk only to 2.1 million pixels it would be up to 1,024 image tokens, about 85 s of reading.

## Decision

For Qwen3-VL (2B and 4B), the longest image edge is capped at **560 px** for `dl`, `passport` and `aadhaar` (`ModelProfiles.QWEN_DOCUMENT_MAX_EDGE`, `useQwenDocumentResize`; `ExtractionPlan.ImagePrep.QWEN_DOC`; `MainActivity` uses the existing `downscaleForMiniCpm` resize with that edge). Images already smaller are not enlarged. Token cap, compact prompts and grammars are unchanged.

**Aadhaar was added on 2026-10-26 at the owner's request.** Qwen on `aadhaar` keeps its own prompt and grammar (`useQwenAadhaarProfile`, which asks for an address flag), but its image is now 560 px. That profile was validated at full size; the 560 px numbers below were measured with the compact prompt, not with that profile, so the in-app result with the profile is still to be seen. Qwen on `pan_full` keeps its 480 px path.

## Evidence (test server, Qwen3-VL 2B, warm server, compact prompts, images not enlarged, 5 cards each)

| Input | DL | Passport | Aadhaar (compact prompt, not the app's profile) |
|---|---|---|---|
| Full size | 20/20 on the four cards that finished, 29 to 75 s | **33/34**, 63 to 74 s | 22/24, 21 to 87 s |
| **560 px** | 19/25, **15 to 19 s** | 28/34, **22 to 24 s** | 19/24, 11 to 17 s |
| 400 px | 14/25, 12 to 14 s | 19/34, 14 to 16 s | 17/24, 8 to 11 s |

400 px was rejected: it saves only 3 to 8 s over 560 px and loses many more fields (the model starts inventing names and numbers).

## Aadhaar check with Qwen's own profile (2026-10-26)

In the app (Run 23, `aa_1`, 560 px): 28.2 s with a cold start; number 6060 instead of 6606, name "Megha" for "Meghna", address end and PIN wrong; the Verhoeff checksum rule marked it LOW. Smoke test on the test server with the same profile, three cards, warm server:

| Size | Seconds | Core fields right (12) | Address and PIN |
|---|---|---|---|
| 560 px | 23 to 37 | 10 | `aa_1` PIN wrong, address looped once |
| 700 px | 29 to 45 | 10 | `aa_1` address and PIN right |
| 800 px | 35 to 59 | 11 | `aa_1` address and PIN right |

The front-only card (`aa_3`) gets an invented address and PIN at every size, so that is a prompt or grammar problem. Three cards and one run each, so only indicative. **The owner decided to keep 560 px**; 700 px stays an option if Aadhaar addresses matter more than 6 to 10 s.

## Consequences

- Qwen on DL and passport takes 15 to 25 s instead of 30 to 75 s, and large photos no longer cost 85 s.
- **Accuracy drops by about 5 fields per set against full size**: licence-number digits are dropped and issue and validity dates swapped on DL; on passport, names lose a word, and dates and places are misread. These are the fields that matter most on a licence.
- A size between 560 px and full size was smoke-tested on Aadhaar only (below). Other documents: not measured.
- Measured from the command line with images resized by `sips`; the app's own resize (`Bitmap.createScaledBitmap`, JPEG 92) has not yet been run for Qwen on a phone.

## Alternatives

- Keep full size: most accurate, 60 to 75 s on large images (about 85 s on a camera photo).
- Test about 700 px first.
- Cap the token budget of the Qwen server instead (a cap near 400 makes the model shrink the picture itself); not measured, and the cap is shared by every document type.
