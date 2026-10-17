#!/bin/bash
# Starts the visiting-card API on http://0.0.0.0:8000 (office: http://10.0.3.99:8000).
# The app starts llama-server itself and stops it again on Ctrl+C.
# Use another model:  MODEL_PATH=... MMPROJ_PATH=... ./visiting_card_api/start.sh
cd "$(dirname "$0")/.." || exit 1
exec .venv/bin/uvicorn app:app --app-dir visiting_card_api --host 0.0.0.0 --port "${PORT:-8000}"
