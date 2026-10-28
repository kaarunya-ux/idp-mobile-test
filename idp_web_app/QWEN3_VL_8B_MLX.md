# Qwen3-VL-8B on Mac mini (MLX): test findings

Date: 2026-10-23 · IDP web extraction (PAN, Aadhaar) · Status: findings only, no decision yet

## Setup

| | |
|---|---|
| Machine | Mac mini "mini3" (10.0.3.66), 24 GB unified memory |
| Model | Qwen3-VL-8B-Instruct, 4-bit (MLX), 5.4 GB |
| Engine | MLX `mlx_vlm.server` 0.7.10, continuous batching (`--max-num-seqs 8`) |
| Test | Same as the earlier tests: 10 real documents (4 PAN, 6 Aadhaar), 20 requests per level, 1 / 2 / 4 / 8 users at the same time, every request a unique copy of its image, same prompts |
| Image tokens | Same as the Qwen3.5-4B (both cut images into 32 px pieces; e.g. 658 vs 668, 1102 vs 1113), no scaling |
| Compared with | Qwen3.5-4B and Qwen2.5-VL-3B on MLX, same machine and test (see LLAMACPP_VS_MLX.md, QWEN2.5_VL_3B_MLX.md) |

"Typical" = median wait (half the users waited less). "Slow" = 95th percentile (1 in 20 waited longer).
The 3B figures are from its matched-token run.

## Wait per user

| Users at the same time | 8B typical / slow | 4B MLX typical / slow | 3B typical / slow |
|---|---|---|---|
| 1 | 6.3 s / 12.4 s | 3.9 s / 7.4 s | 3.2 s / 6.3 s |
| 2 | 10.7 s / 18.4 s | 6.1 s / 11.0 s | 5.6 s / 9.9 s |
| 4 | 16.7 s / 34.7 s | 9.9 s / 19.7 s | 9.6 s / 19.5 s |
| 8 | 37.3 s / 80.1 s | 21.9 s / 40.6 s | 18.9 s / 36.2 s |

## Throughput, accuracy and errors

| Users at the same time | 8B docs/min | 4B docs/min | 3B docs/min | 8B accuracy | 4B accuracy | 3B accuracy |
|---|---|---|---|---|---|---|
| 1 | 8.2 | 13.0 | 15.7 | 98.2% | 100% | 87.5% |
| 2 | 10.1 | 16.7 | 19.1 | 96.4% | 99.1% | 84.8% |
| 4 | 11.1 | 18.8 | 20.2 | 96.4% | 99.1% | 84.8% |
| 8 | 10.9 | 19.1 | 18.8 | 95.5% | 99.1% | 85.0% |

Failed requests: none of 80 (every answer was valid JSON).

Accuracy = fields matching the correct values (56 fields per round of the 10 images).

## Where the 8B missed

| Field | Misses (all 4 levels, 80 requests) |
|---|---|
| Address (Aadhaar) | 14 |
| Gender (Aadhaar) | 1 (at 8 users) |
| All other fields | 0 |

- The address misses were on the same two Aadhaar images (aadhaar_3, aadhaar_4) every time: aadhaar_4 at
  every level, aadhaar_3 from 2 users up. In the smoke test the 8B returned no address at all for these two;
  the 4B read both.
- The other 4 Aadhaar addresses matched at 97-100% similarity.
- PAN was read correctly every time.

## Observations

- The 8B was the slowest of the three at every level: about 1.6x the 4B's wait (6.3 vs 3.9 s for one user,
  37.3 vs 21.9 s at 8 users).
- Throughput stopped rising after 4 users (about 11 documents per minute), against about 19-20 for the 4B
  and the 3B.
- Accuracy was 95-98%: higher than the 3B (about 85%), a little lower than the 4B (99-100%).
- Almost all misses were one field (address) on two images, so a prompt change for those layouts might
  change the result.
- No invalid answers, even without schema enforcement (MLX runs with `SEND_SCHEMA=false`).

## Limits

- Only PAN and Aadhaar were tested (10 images). Driving licence is not tested yet.
- One machine: Mac mini .66. Other machines will give different numbers.
- Prompts were written and tuned on the Qwen3.5-4B.
