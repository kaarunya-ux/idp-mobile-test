# Qwen3.6-35B-A3B on Mac mini (llama.cpp): test findings

Date: 2026-10-23 · IDP web extraction (PAN, Aadhaar) · Status: findings only, no decision yet

## Setup

| | |
|---|---|
| Machine | Mac mini "mini3" (10.0.3.66), 24 GB unified memory |
| Model | Qwen3.6-35B-A3B (mixture of experts: 35B total, about 3B used per token), unsloth `UD-IQ4_XS` (17.7 GB) + `mmproj-F16` vision (0.9 GB) |
| Engine | llama.cpp `llama-server` b11429, 8 parallel slots (`-np 8 -c 32768 -fa on -ngl 99 --jinja`) |
| GPU memory | Raised to 21 GB before start (`sudo sysctl iogpu.wired_limit_mb=21504`); the macOS default (about 16 GB) is below the model's size. Resets on reboot |
| Test | Same as the earlier tests: 10 real documents (4 PAN, 6 Aadhaar), 20 requests per level, 1 / 2 / 4 / 8 users at the same time, every request a unique copy of its image, same prompts |
| App settings | `SEND_SCHEMA=true` (llama-server enforces the output format), `IMAGE_SCALE=1`, thinking off |
| Compared with | The other models on the same machine and test; the 4B on both engines (see LLAMACPP_VS_MLX.md) |

"Typical" = median wait (half the users waited less). "Slow" = 95th percentile (1 in 20 waited longer).

## Wait per user

| Users at the same time | 35B-A3B llama.cpp | 4B llama.cpp | 4B MLX | 8B MLX | 9B MLX |
|---|---|---|---|---|---|
| 1 | 4.6 s / 9.0 s | 4.2 s / 8.4 s | 3.9 s / 7.4 s | 6.3 s / 12.4 s | 6.9 s / 13.9 s |
| 2 | 12.7 s / 21.5 s | 9.1 s / 17.0 s | 6.1 s / 11.0 s | 10.7 s / 18.4 s | 12.7 s / 21.5 s |
| 4 | 22.7 s / 46.0 s | 15.5 s / 32.9 s | 9.9 s / 19.7 s | 16.7 s / 34.7 s | 18.6 s / 40.4 s |
| 8 | 42.7 s / 79.9 s | 24.1 s / 45.7 s | 21.9 s / 40.6 s | 37.3 s / 80.1 s | 40.3 s / 74.1 s |

(typical / slow)

## Throughput, accuracy and errors

| Users at the same time | 35B-A3B docs/min | 4B llama.cpp docs/min | 4B MLX docs/min | 35B-A3B accuracy | 4B llama.cpp accuracy | 4B MLX accuracy |
|---|---|---|---|---|---|---|
| 1 | 10.5 | 11.6 | 13.0 | 98.2% | 100% | 100% |
| 2 | 8.6 | 11.6 | 16.7 | 98.2% | 100% | 99.1% |
| 4 | 8.8 | 12.4 | 18.8 | 98.2% | 100% | 99.1% |
| 8 | 9.6 | 16.9 | 19.1 | 98.2% | 98.2% | 99.1% |

Failed requests: none of 80.

Accuracy = fields matching the correct values (56 fields per round of the 10 images).

## Where the 35B-A3B missed

| Field | Misses (all 4 levels, 80 requests) |
|---|---|
| PAN number (pan_2) | 8 |
| All other fields | 0 |

- One field on one image, missed on every request at every level, so accuracy was the same 98.2% at every
  level. The 9B misses the same PAN number; the 4B reads it.
- The two Aadhaar addresses the 8B and 9B miss (aadhaar_3, aadhaar_4) were read correctly every time.

## Time per request, reading the image vs writing the answer

Averages from the model server's own timings.

| Users at the same time | Reading (image + prompt) | Writing the answer | Writing speed per request |
|---|---|---|---|
| 1 | 2.5 s | 2.8 s | 30 tokens/s |
| 2 | 4.0 s | 8.0 s | 11 tokens/s |
| 4 | 5.5 s | 18.1 s | 6 tokens/s |
| 8 | 12.5 s | 24.9 s | 5 tokens/s |

## Observations

- With one user it is close to the 4B (4.6 vs 4.2 s on llama.cpp) and the fastest of the larger models.
- With 2 or more users it does not gain throughput: documents per minute stayed at about 9-10 from 1 to 8
  users, while the 4B rose from 12 to 17 (llama.cpp) and 13 to 19 (MLX).
- The slowdown is in writing the answer: one request writes at 30 tokens/s, but two at once write at 11
  each (22 together, less than one alone). A likely cause is the mixture-of-experts design: each request
  uses different experts, so serving several at once reads much more of the 17.7 GB per step instead of
  sharing it. This is an explanation, not something measured here.
- Accuracy was 98.2% at every level, and its only miss differs from the 8B's and 9B's misses.
- Its files alone are 18.6 GB of the 24 GB (memory in use was not measured), and it needs the GPU memory
  limit raised; nothing else large can run on the Mac alongside it.

## Limits

- Only PAN and Aadhaar were tested (10 images). Driving licence is not tested yet.
- One machine: Mac mini .66. Other machines will give different numbers.
- One engine: llama.cpp. The MLX version is too large for 24 GB; a pruned MLX version is to be tested next.
- Prompts were written and tuned on the Qwen3.5-4B.
