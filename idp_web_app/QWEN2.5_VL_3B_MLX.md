# Qwen2.5-VL-3B on Mac mini (MLX): test findings

Date: 2026-10-23 · IDP web extraction (PAN, Aadhaar) · Status: findings only, no decision yet

## Setup

| | |
|---|---|
| Machine | Mac mini "mini3" (10.0.3.66), 24 GB unified memory |
| Model | Qwen2.5-VL-3B-Instruct, 4-bit (MLX) |
| Engine | MLX `mlx_vlm.server` 0.7.10, continuous batching (`--max-num-seqs 8`) |
| Test | Same as the Qwen3.5-4B tests: 10 real documents (4 PAN, 6 Aadhaar), 20 requests per level, 1 / 2 / 4 / 8 users at the same time, every request a unique copy of its image, same prompts |
| Compared with | Qwen3.5-4B on MLX, same machine and test (see LLAMACPP_VS_MLX.md) |

"Typical" = median wait (half the users waited less). "Slow" = 95th percentile (1 in 20 waited longer).

## Two runs

Qwen2.5-VL cuts an image into 28 px pieces, Qwen3.5 into 32 px pieces, so the same image gives Qwen2.5-VL
about 25-35% more tokens to read.

- **Run 1, same images:** the 3B saw exactly the same images as the 4B (more tokens for the 3B).
- **Run 2, same tokens:** images scaled by 0.875 (= 28/32) for the 3B, so it read exactly as many image tokens
  as the 4B (checked per image: e.g. 669 vs 668, 1113 vs 1113).

## Wait per user

| Users at the same time | 3B run 1 typical / slow | 3B run 2 typical / slow | 4B MLX typical / slow |
|---|---|---|---|
| 1 | 3.6 s / 7.0 s | 3.2 s / 6.3 s | 3.9 s / 7.4 s |
| 2 | 6.2 s / 11.3 s | 5.6 s / 9.9 s | 6.1 s / 11.0 s |
| 4 | 11.7 s / 17.2 s | 9.6 s / 19.5 s | 9.9 s / 19.7 s |
| 8 | 23.4 s / 45.7 s | 18.9 s / 36.2 s | 21.9 s / 40.6 s |

## Throughput, accuracy and errors

| Users at the same time | 3B run 1 docs/min | 3B run 2 docs/min | 4B MLX docs/min | 3B run 1 accuracy | 3B run 2 accuracy | 4B MLX accuracy |
|---|---|---|---|---|---|---|
| 1 | 12.5 | 15.7 | 13.0 | 84.0% | 87.5% | 100% |
| 2 | 15.1 | 19.1 | 16.7 | 84.9% | 84.8% | 99.1% |
| 4 | 15.8 | 20.2 | 18.8 | 83.2% | 84.8% | 99.1% |
| 8 | 16.9 | 18.8 | 19.1 | 84.8% | 85.0% | 99.1% |

Failed requests (answer was not valid JSON): run 1: 3 of 80 (at 1, 2 and 4 users) · run 2: 1 of 80 (at 8 users) ·
4B: none.

Accuracy = fields matching the correct values (56 fields per round of the 10 images).

## Where the 3B missed

| Field | Run 1 misses | Run 2 misses |
|---|---|---|
| Aadhaar number | 28 | 18 |
| Address | 25 | 28 |
| Name (Aadhaar) | 0 | 10 |
| Date of birth | 8 | 0 |
| PIN code | 7 | 8 |
| PAN fields | 0 | 0 |

All misses were on Aadhaar documents (cards and letters); PAN was read correctly every time on both runs.

## Observations

- Matching the token count made the 3B faster than the 4B at every level: 3.2 vs 3.9 s for one user,
  18.9 vs 21.9 s at 8 users, up to 20 documents per minute.
- Its accuracy was about 85% in both runs, against 99-100% for the 4B.
- The accuracy was the same with 1 user as with 8 (84-88%), so the misses are not caused by load:
  they appear even when the model handles one request at a time.
- PAN results matched the 4B; the gap is entirely on Aadhaar (number, address, name, PIN code).
- MLX does not enforce the output format for this setup (see LLAMACPP_VS_MLX.md), which may explain the few
  invalid answers.

## Limits

- Only PAN and Aadhaar were tested (10 images). Driving licence is not tested yet.
- One machine: Mac mini .66. Other machines will give different numbers.
- Prompts were written and tuned on the Qwen3.5-4B; a prompt tuned for the 3B might change its accuracy.
