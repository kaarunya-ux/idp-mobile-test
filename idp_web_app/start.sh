#!/bin/bash
# Start this service in the foreground. Settings come from .env next to this script
# (copy .env.example to .env first). Uses ./.venv if present, else uvicorn from PATH.
cd "$(dirname "$0")" || exit 1
if [ -f .env ]; then set -a; . ./.env; set +a; fi
UVICORN=uvicorn
[ -x .venv/bin/uvicorn ] && UVICORN=.venv/bin/uvicorn
exec "$UVICORN" app:app --host "${HOST:-0.0.0.0}" --port "${PORT:-8020}"
