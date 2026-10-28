#!/bin/bash
# Run one vision-language model with llama-server on the model host (mini3) for the IDP web app.
#
#   MODEL=/path/model.gguf MMPROJ=/path/mmproj.gguf ALIAS=qwen3-vl-4b NP=4 ./start_model.sh
#
# Settings via environment variables:
#   MODEL, MMPROJ     the model and its vision projector (.gguf), both required
#   ALIAS             model id the API reports (and the benchmark records), e.g. qwen3-vl-8b
#   NP                parallel slots = requests served at the same time (default 1)
#   CTX_PER_SLOT      context per slot in tokens (default 4096: image ~1,000-2,000 + prompt + answer)
#   PORT              default 8081
#   LLAMA_API_KEY     if set, clients must send it (set the same value in the app's .env)
#   LLAMA_SERVER_BIN  path to llama-server (default: llama-server on PATH)
#   GPU_LAYERS        layers on the GPU, default 99 (all)
#
# Total context = NP x CTX_PER_SLOT. Memory grows with it (KV cache), on top of the weights.
PORT="${PORT:-8081}"
NP="${NP:-1}"
CTX_PER_SLOT="${CTX_PER_SLOT:-4096}"
BIN="${LLAMA_SERVER_BIN:-llama-server}"

[ -n "$MODEL" ] && [ -n "$MMPROJ" ] || { echo "Set MODEL and MMPROJ to the two .gguf files"; exit 1; }
for f in "$MODEL" "$MMPROJ"; do
  [ -f "$f" ] || { echo "Missing $f"; exit 1; }
done
command -v "$BIN" >/dev/null || { echo "llama-server not found (set LLAMA_SERVER_BIN)"; exit 1; }
ALIAS="${ALIAS:-$(basename "$MODEL" .gguf)}"

HELP="$("$BIN" --help 2>&1)"
args=(-m "$MODEL" --mmproj "$MMPROJ" --host 0.0.0.0 --port "$PORT" --alias "$ALIAS"
      -ngl "${GPU_LAYERS:-99}" -np "$NP" -c $((NP * CTX_PER_SLOT)) --metrics)
# Flag spellings differ between llama.cpp versions; use whichever this build has.
if grep -q "on|off|auto" <<<"$HELP"; then args+=(-fa on); else args+=(-fa); fi
# Keep the weights locked in RAM so an idle machine never pages them out between requests.
if grep -q -- "--load-mode" <<<"$HELP"; then args+=(--load-mode mmap+mlock); else args+=(--mlock); fi
grep -q -- "--lazy-mode" <<<"$HELP" && args+=(--lazy-mode off)
grep -q -- "--no-webui" <<<"$HELP" && args+=(--no-webui)
# Needed for chat_template_kwargs (the app turns thinking off for hybrid models like Qwen3.6).
grep -q -- "--jinja" <<<"$HELP" && args+=(--jinja)
[ -n "$LLAMA_API_KEY" ] && args+=(--api-key "$LLAMA_API_KEY")

echo "Starting: $BIN ${args[*]/$LLAMA_API_KEY/***}"
exec "$BIN" "${args[@]}"
