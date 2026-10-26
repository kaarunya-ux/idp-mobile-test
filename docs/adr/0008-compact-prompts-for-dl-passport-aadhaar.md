# 8. Compact prompts and grammars for DL, passport and Aadhaar

Status: Proposed · Date: 2026-10-23 · Deciders: mobile extraction owner and team lead (to approve)

## Context

For driving licence (`dl`), passport and Aadhaar the app sent the model a long instruction prompt (about 800 tokens with the system prompt), the document's full JSON schema, and asked for every field the schema lists (eleven for DL). On the Galaxy M35 the models read 10 to 19 tokens per second, so the prompt text alone cost more than the answer:

- In-app LFM2 DL scan before the change: 1,075 prompt tokens (43.6 s to read), 146 answer tokens (19.9 s), **63.7 s** in total (Run 2 in the run log).
- In the first server test the full DL prompt was 1,214 tokens and was killed before finishing.

## Decision

For LFM2-VL, MiniCPM-V and Qwen3-VL on `dl` and `passport`, and for LFM2-VL and MiniCPM-V on `aadhaar`, the app uses a short prompt, no system prompt and a small output grammar, instead of the long prompt and the full schema:

- Prompts: `assets/prompts/user_dl_compact.v1.md`, `user_passport_compact.v1.md`, `user_aadhaar_compact.v1.md` (4 to 7 lines each).
- Grammars: `assets/grammars/dl_compact.gbnf`, `passport_compact.gbnf`, `aadhaar_compact.gbnf` (fix the JSON shape and the character patterns, such as three groups of four digits for the Aadhaar number).
- `ModelProfiles.useCompactDocument` selects the case, `ExtractionPlan.Plan.compact` carries it, and `DlRules`, `PassportRules` and `AadhaarRules` take a `compact` flag so the check grades only the fields that are asked for.
- Qwen keeps its own Aadhaar profile (`useQwenAadhaarProfile`). Other model families keep the full prompt.

**Fields that are no longer asked for (a product decision, to confirm):**

| Document | Still asked | Dropped |
|---|---|---|
| DL | licence number, name, DOB, issue date, validity (NT) | address, validity (TR), blood group, organ donor, vehicle classes |
| Passport | number, name, DOB, gender, place of birth, issue and expiry dates | issuing authority, nationality |
| Aadhaar (LFM2, MiniCPM) | number, name, DOB, gender | address, PIN |

## Evidence

Measured 2026-10-23 on the phone (warm server, resized images, five or six cards per document):

| Case | Full prompt | Compact prompt |
|---|---|---|
| LFM2 DL, in the app | 63.7 s, 1,075 prompt tokens | 24.7 to 37.7 s, 242 to 314 tokens |
| Qwen DL, test server | over 50 s on all five cards | 29 to 46 s (3 cards answered, 15/15 fields) |
| MiniCPM DL, test server | 33 to 43 s | 23 to 25 s |
| LFM2 passport, in the app | | 31 to 36 s, 13/14 fields |
| LFM2 Aadhaar, in the app | | 29 to 43 s (one-shot), 7/8 fields |

In the app, the same card with the same prompt and grammar gave the same answer as the test server in 10 of 11 comparisons (the one difference came from the test script enlarging a tiny image).

## Consequences

- Scans are about 2 to 3 times faster on DL, and the prompt is a short, versioned file with its hash pinned in `PromptFileTest` (LLM-02).
- **Less data comes back.** Anyone who needs the address or the vehicle classes needs a second pass or a different prompt. This needs the team lead's decision.
- The grammar forces a well-formed answer but not a correct one. Observed errors: licence numbers cut short or with the label `DL NO` copied in, a wrong issue date, a missing passport surname. Three rule fixes made the same day catch some of them: a real calendar check, rejection of a licence number that starts with `DL NO`, and an issue year lower bound of 1950 instead of 1990 (`ExtractionRules`, `DlRules`).
- Accuracy is measured on very few cards (4 DL, 2 passport, 2 Aadhaar in the app) and no camera photos. An evaluation set is still needed (ML-11, ML-12).

## Alternatives

- Keep the full prompt and schema: accurate on field coverage, but 60 to 75 s on LFM2.
- Shorten the prompt but still ask for all eleven DL fields: estimated about 33 s, not measured.
- Two passes (key fields first, address on demand): not built.
