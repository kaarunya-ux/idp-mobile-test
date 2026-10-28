# Qwen3.5-4B on Mac mini: llama.cpp vs MLX

Date: 2026-10-23 · IDP web extraction (PAN, Aadhaar)

## Setup

| | |
|---|---|
| Machine | Mac mini "mini3" (10.0.3.66), 24 GB unified memory |
| Model | Qwen3.5-4B, 4-bit, with vision (same model on both engines) |
| Engine A | llama.cpp `llama-server`, 8 parallel slots (`-np 8 -c 32768 -fa on`) |
| Engine B | MLX `mlx_vlm.server` 0.7.10, continuous batching (`--max-num-seqs 8`) |
| Test images | 10 real documents: 4 PAN, 6 Aadhaar (cards and letters) |
| Requests | 20 per level (each image twice), at 1, 2, 4 and 8 users at the same time |
| Fairness | Every request sends a unique copy of its image, so neither engine can reuse a cached image. Same prompts, same image sizes (max 1280 px, min 768 px), same output format on both |

"Typical" = median wait (half the users waited less). "Slow" = 95th percentile (1 in 20 waited longer).

## Wait per user

| Users at the same time | llama.cpp typical | llama.cpp slow | MLX typical | MLX slow |
|---|---|---|---|---|
| 1 | 4.2 s | 8.4 s | **3.9 s** | **7.4 s** |
| 2 | 9.1 s | 17.0 s | **6.1 s** | **11.0 s** |
| 4 | 15.5 s | 32.9 s | **9.9 s** | **19.7 s** |
| 8 | 24.1 s | 45.7 s | **21.9 s** | **40.6 s** |

## Throughput and accuracy

| Users at the same time | llama.cpp docs/min | MLX docs/min | llama.cpp accuracy | MLX accuracy |
|---|---|---|---|---|
| 1 | 11.6 | **13.0** | 100% | 100% |
| 2 | 11.6 | **16.7** | 100% | 99.1% |
| 4 | 12.4 | **18.8** | 100% | 99.1% |
| 8 | 16.9 | **19.1** | 98.2% | 99.1% |

Accuracy = fields matching the correct values (56 fields per round). Every miss on either engine was the
same field: the name on one Aadhaar card, wrong by one character, once per level on MLX and twice at 8 users
on llama.cpp. No errors or timeouts on either engine.

## Verdict

**MLX is better on this Mac.**

- 2 to 4 users at once: MLX cuts the wait by about 35% (2 users 9.1 → 6.1 s, 4 users 15.5 → 9.9 s) and
  handles about 50% more documents per minute.
- 1 user: MLX is slightly faster (3.9 vs 4.2 s).
- 8 users: both reach the machine's limit (about 17-19 documents per minute); waits are 22-24 s on either.
- Accuracy is the same on both.

Capacity of one Mac mini with MLX: about **4 users at the same time** with a typical wait under 10 s
(llama.cpp: about 2). More users at once need more machines or a GPU server.

## Limits

- Only PAN and Aadhaar were tested (10 images). Driving licence is not tested yet.
- One machine: Mac mini .66. Other machines will give different numbers.
- A quick check from the test page agreed: one PAN card sent by 2 / 4 / 8 users at once took about
  6 / 9 / 16 s (typical) on MLX.

Note for MLX: its server's JSON-schema enforcement corrupts the first value of the answer, so the app does not
send the schema to MLX (`SEND_SCHEMA=false`) and checks every value against the format rules itself.
