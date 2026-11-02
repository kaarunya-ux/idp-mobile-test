#!/bin/bash
# Start the model host on this Mac: Qwen3.5-4B (4-bit MLX) served by mlx_vlm.server, OpenAI-compatible,
# on 0.0.0.0:8082. Paths are relative to the repo root; caffeinate keeps the Mac awake while it runs.
#   MODEL=models/... PORT=8082 SEQS=8 ./idp_web_app/model_host/start_mlx.sh
cd "$(dirname "$0")/../.." || exit 1
MODEL="${MODEL:-models/Qwen3.5-4B-MLX-4bit}"
PORT="${PORT:-8082}"
SEQS="${SEQS:-8}"   # requests served at the same time (continuous batching)
exec caffeinate -is .venv-mlx/bin/python -m mlx_vlm.server --model "$MODEL" --host 0.0.0.0 --port "$PORT" \
    --max-num-seqs "$SEQS"
