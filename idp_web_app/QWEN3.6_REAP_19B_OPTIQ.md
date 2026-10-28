# Qwen3.6-35B-A3B pruned to 19B on Mac mini (MLX, OptiQ server): test findings

Date: 2026-10-23 · IDP web extraction (PAN, Aadhaar) · Status: findings only, no decision yet

## Setup

| | |
|---|---|
| Machine | Mac mini "mini3" (10.0.3.66), 24 GB unified memory |
| Model | `mlx-community/Qwen3.6-35B-A3B-OptiQ-4bit-REAP-19B`: Qwen3.6-35B-A3B with half its experts removed (18.8B total, about 3B used per token, same as the parent), 4-bit, 13.8 GB on disk incl. vision part (0.9 GB) and MTP head (1.6 GB, not used) |
| Engine | OptiQ `optiq serve` (built on mlx-lm), not `mlx_vlm.server`: this model's vision part only loads under OptiQ |
| Server flags | `--max-concurrent 8 --no-stream-experts --api-key …`; no `--mtp`, no `--kv-bits` (fp16 KV), to match the other tests |
| App settings | `LLAMA_MODEL=…REAP-19B-MLX-4bit:no-think` (thinking off), `SEND_SCHEMA=false`, `IMAGE_SCALE=1`, `LLAMA_API_KEY` set |
| Test | Same as the earlier tests: 10 real documents (4 PAN, 6 Aadhaar), 20 requests per level, 1 / 2 / 4 / 8 users at the same time, every request a unique copy of its image, same prompts |
| Image tokens | Same as the Qwen3.5-4B (e.g. 668, 1113) |

"Typical" = median wait, "slow" = 95th percentile (1 in 20 waited longer).

## Results

| Users at the same time | Typical / slow wait | Docs/min | Accuracy | Failed |
|---|---|---|---|---|
| 1 | 5.0 s / 9.2 s | 11.4 | 97.3% | 0 |
| 2 | 9.4 s / 17.2 s | 11.8 | 97.3% | 0 |
| 4 | 19.0 s / 26.7 s | 11.8 | 98.2% | 0 |
| 8 | 38.2 s / 42.1 s | 11.5 | 98.2% | 0 |

## Compared with the other models (typical wait / accuracy)

| Users | 19B pruned (OptiQ) | 35B-A3B (llama.cpp) | 4B (MLX) | 4B (llama.cpp) | 8B (MLX) | 9B (MLX) | 3B (MLX, matched tokens) |
|---|---|---|---|---|---|---|---|
| 1 | 5.0 s / 97.3% | 4.6 s / 98.2% | 3.9 s / 100% | 4.2 s / 100% | 6.3 s / 98.2% | 6.9 s / 94.6% | 3.2 s / 87.5% |
| 2 | 9.4 s / 97.3% | 12.7 s / 98.2% | 6.1 s / 99.1% | 9.1 s / 100% | 10.7 s / 96.4% | 12.7 s / 93.8% | 5.6 s / 84.8% |
| 4 | 19.0 s / 98.2% | 22.7 s / 98.2% | 9.9 s / 99.1% | 15.5 s / 100% | 16.7 s / 96.4% | 18.6 s / 94.6% | 9.6 s / 84.8% |
| 8 | 38.2 s / 98.2% | 42.7 s / 98.2% | 21.9 s / 99.1% | 24.1 s / 98.2% | 37.3 s / 95.5% | 40.3 s / 92.9% | 18.9 s / 85.0% |

Docs/min at 8 users: 19B 11.5 · 35B 9.6 · 4B MLX 19.1 · 4B llama.cpp 16.9 · 8B 10.9 · 9B 10.6 · 3B 18.8.

## Where it missed

| Image / field | Misses (80 requests) |
|---|---|
| pan_2 PAN number | 8 (every request) |
| pan_1 PAN number | 1 (at 2 users) |
| aadhaar_5 name | 1 (at 1 user) |

- The pan_2 PAN number is also missed by the 35B-A3B and the 9B; the 4B reads it.
- All Aadhaar addresses were read correctly, including aadhaar_3 and aadhaar_4 (missed by the 8B and 9B).

## Observations

- Close to the full 35B-A3B in accuracy (97-98% vs 98.2%) at about 40% less size (13.8 vs 18.6 GB),
  (the raised GPU memory limit from the 35B test was still set; this size should fit the default).
- Faster than the 35B-A3B on llama.cpp from 2 users up (9.4 vs 12.7 s at 2, 38.2 vs 42.7 s at 8), and its
  slow waits stay close to its typical waits (42 s vs 80 s at 8 users).
- Throughput stays flat at about 11-12 documents per minute from 1 to 8 users: like the 35B-A3B, it gains
  nothing from serving several users at once. The 4B and 3B reach 19-20.
- No invalid answers without schema enforcement.

## Limits

- Only PAN and Aadhaar were tested (10 images). Driving licence is not tested yet.
- One machine: Mac mini .66.
- Different server (OptiQ) from the other MLX tests (`mlx_vlm.server`), so engine and model differences are
  mixed in these numbers.
- MTP speculative decoding (`--mtp`, about 1.4x faster writing per the model card) was not tested.
- Prompts were written and tuned on the Qwen3.5-4B.
