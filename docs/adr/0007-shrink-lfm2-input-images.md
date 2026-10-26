# 7. Shrink the images sent to LFM2-VL (PAN to 500 px, Aadhaar, DL and passport to 560 px)

Status: Proposed · Date: 2026-10-23 · Deciders: mobile extraction owner and team lead (to approve)

## Context

The libraries built from the pinned llama.cpp commit `000bee5` split an LFM2-VL image into 512 px tiles of 256 tokens each when the rounded image area exceeds twice the pixel budget of the token cap (upstream PR #27057, 2026-08-18). Before this change the app sent LFM2 a PAN card at its own size (`downscaleForLfm2` capped the longest edge at 1,280 px, which an 860 px card never reaches) and sent Aadhaar, DL and passport at their original size. With the new libraries:

- A PAN card at cap 128 became 15 tiles and about 3,800 tokens: 85 to 95+ s, against 24 to 36 s with the libraries the app shipped with.
- Aadhaar, DL and passport images produced 300 to 1,000 tokens (a 2743x1904 photo tiled to 2,589). LFM2 reads about 10 to 15 tokens per second on the Galaxy M35, so the large ones took 60 to over 170 s.
- Qwen3-VL and MiniCPM-V are not affected: they use a different preprocessor, or the app already shrinks the image for them.

Measurements and method: `docs/mobile-extraction/ON_DEVICE_LATENCY_AND_ACCURACY_FINDINGS.md` and section 5 of `STANDARDIZATION_HANDOFF.md`.

## Decision

For LFM2-VL only, the longest image edge is capped before the model sees it:

- `pan_full`: 500 px (`ModelProfiles.LFM2_PAN_MAX_EDGE`).
- `aadhaar`, `dl`, `passport`: 560 px (`ModelProfiles.LFM2_DOCUMENT_MAX_EDGE`).

`ExtractionPlan` carries the edge in `Plan.maxEdge`; `MainActivity.downscaleForLfm2` takes it as a parameter. Every other model and document type is unchanged. Token caps, prompts, grammars and schemas are unchanged.

## Evidence (command line on the phone, new libraries, app flags)

| Case | Before | After |
|---|---|---|
| PAN, five cards | 85 to 95+ s | 22 to 26 s, 24/25 fields |
| DL, warm server | 33 s, 81 s and over 170 s on three of the five cards | 23 to 29 s on three cards, 13/15 fields |
| Passport, five cards, warm server | 86 s on the one card that finished, the rest failed | 25 to 29 s, 30/34 fields |
| Aadhaar, six cards, warm server | no valid run | 14 to 22 s, 19/24 fields |

Sizes tried for PAN: 400 px misread digits, 500 and 600 px gave the same fields, 600 px is closer to the tiling limit.

## Consequences

- LFM2 becomes usable on these documents (14 to 29 s) when the phone has free memory. It still needs about 2.2 to 2.7 GB of RAM and stalls when memory runs short.
- **Accuracy cost is not fully known.** For Qwen on passport the same resize cost 5 of 34 fields; for LFM2 there is no full-size baseline with valid answers. Small print (addresses, licence numbers) may blur.
- The measurements resized the images with `sips` and ran from the command line. The app's own resize (`Bitmap.createScaledBitmap`, JPEG quality 92) and the in-app scan have not been run on a phone.
- The values are constants with their measurement beside them, pinned by unit tests (`ExtractionPlanTest`, `ModelProfilesTest`). Changing them needs a new measurement (ML-07).
- Not covered: camera photos, other document types (the Trinidad and Tobago documents keep the original image for LFM2), and the 4B model.

## Alternatives

- **A. Pin an older llama.cpp commit** (before 2026-08-18). Not tested; it would need to keep the other models working.
- **C. Keep the old `libmtmd.so`.** It works with the new libraries (22 s on one card) but has no documented source and cannot be rebuilt, which conflicts with BLD-02 and DPL-01/02.
- **Lower the token cap instead.** Most of these images are below the cap, so the cap does little; untested for small print.
