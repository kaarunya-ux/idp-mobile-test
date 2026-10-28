# Qwen3.5-9B on Mac mini (MLX): test findings

Date: 2026-10-23 · IDP web extraction (PAN, Aadhaar) · Status: findings only, no decision yet

## Setup

| | |
|---|---|
| Machine | Mac mini "mini3" (10.0.3.66), 24 GB unified memory |
| Model | Qwen3.5-9B, 4-bit language part, BF16 vision part built in (MLX), 5.6 GB |
| Engine | MLX `mlx_vlm.server` 0.7.10, continuous batching (`--max-num-seqs 8`) |
| Test | Same as the earlier tests: 10 real documents (4 PAN, 6 Aadhaar), 20 requests per level, 1 / 2 / 4 / 8 users at the same time, every request a unique copy of its image, same prompts |
| Image tokens | Same as the Qwen3.5-4B (32 px pieces; e.g. 668 vs 668), no scaling |
| Thinking | Off (`enable_thinking: false`); answers are about 50-60 tokens for a PAN card |
| Compared with | Qwen3.5-4B, Qwen2.5-VL-3B and Qwen3-VL-8B on MLX, same machine and test |

## App change made for this model

The first smoke test (10 images, one user) had 3 of 10 answers rejected as invalid. They held the right
data but not in the requested format:

- a sentence first, then the array inside a ```json fence (aadhaar_2);
- an object keyed by the field names (`{"aadhaar_number": ...}`) instead of the plain array in order
  (aadhaar_6);
- one more that came and went between runs (aadhaar_4).

MLX does not enforce the output format (`SEND_SCHEMA=false`), so the app relies on the model following the
prompt. The app's answer reader (`_parse` and `_as_list` in `extract.py`) now also accepts:

- JSON after leading text, or inside a ```json fence anywhere in the answer;
- an object keyed by the field names, when every field name is present (turned into the array in order).

Every value is still checked against the field's format rules as before; an object with other names is
still rejected. After the change the same smoke test had 10 of 10 valid answers.

The change applies to every model. The earlier tests (Qwen3.5-4B, Qwen2.5-VL-3B, Qwen3-VL-8B) ran before it;
they had 0, 1 (matched-token run; 3 in the first run) and 0 invalid answers out of 80, so their results
would barely change.

## Wait per user

"Typical" = median wait (half the users waited less). "Slow" = 95th percentile (1 in 20 waited longer).
The 3B figures are from its matched-token run.

| Users at the same time | 9B typical / slow | 8B typical / slow | 4B MLX typical / slow | 3B typical / slow |
|---|---|---|---|---|
| 1 | 6.9 s / 13.9 s | 6.3 s / 12.4 s | 3.9 s / 7.4 s | 3.2 s / 6.3 s |
| 2 | 12.7 s / 21.5 s | 10.7 s / 18.4 s | 6.1 s / 11.0 s | 5.6 s / 9.9 s |
| 4 | 18.6 s / 40.4 s | 16.7 s / 34.7 s | 9.9 s / 19.7 s | 9.6 s / 19.5 s |
| 8 | 40.3 s / 74.1 s | 37.3 s / 80.1 s | 21.9 s / 40.6 s | 18.9 s / 36.2 s |

## Throughput, accuracy and errors

| Users at the same time | 9B docs/min | 8B docs/min | 4B docs/min | 3B docs/min | 9B accuracy | 8B accuracy | 4B accuracy | 3B accuracy |
|---|---|---|---|---|---|---|---|---|
| 1 | 7.3 | 8.2 | 13.0 | 15.7 | 94.6% | 98.2% | 100% | 87.5% |
| 2 | 8.8 | 10.1 | 16.7 | 19.1 | 93.8% | 96.4% | 99.1% | 84.8% |
| 4 | 9.7 | 11.1 | 18.8 | 20.2 | 94.6% | 96.4% | 99.1% | 84.8% |
| 8 | 10.6 | 10.9 | 19.1 | 18.8 | 92.9% | 95.5% | 99.1% | 85.0% |

Failed requests: none of 80 (every answer was read, with the app change above).

Accuracy = fields matching the correct values (56 fields per round of the 10 images).

## Where the 9B missed

| Field | Misses (all 4 levels, 80 requests) |
|---|---|
| Address (Aadhaar) | 16 |
| PAN number | 8 |
| Gender (Aadhaar) | 3 (at 2 and 8 users) |
| All other fields | 0 |

- The same three images every time: aadhaar_3 and aadhaar_4 (address) and pan_2 (PAN number), missed on
  every request at every level. The 8B misses the same two addresses; the 4B reads all three.
- Gender on aadhaar_6 was missed once at 2 users and twice at 8 users.
- The other 3 PAN cards and the other 4 Aadhaar addresses were read correctly.

## Observations

- The 9B was the slowest of the four at 1, 2 and 4 users, slightly slower than the 8B (6.9 vs 6.3 s for one
  user); at 8 users its slow wait (74 s) was a little below the 8B's (80 s).
- Throughput was about 7-11 documents per minute, similar to the 8B and about half of the 4B and 3B.
- Accuracy was 93-95%, a little below the 8B (95-98%) and the 4B (99-100%), above the 3B (about 85%).
- The misses were almost all fixed per image (the same three images every time), not caused by load:
  one user and 8 users differed by about 2 points.
- Without the app change, about 3 in 10 answers would have been rejected as invalid (first smoke test).

## Limits

- Only PAN and Aadhaar were tested (10 images). Driving licence is not tested yet.
- One machine: Mac mini .66. Other machines will give different numbers.
- Prompts were written and tuned on the Qwen3.5-4B.
