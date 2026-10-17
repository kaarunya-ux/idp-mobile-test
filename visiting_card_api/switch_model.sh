#!/bin/bash
# Restart the API on another model:  ./visiting_card_api/switch_model.sh 4b|8b
# Stops the running API (and its llama-server), starts the chosen model in the background,
# and waits until it answers /health. Takes under a minute; requests fail meanwhile.
# This lasts until the next restart; to make a model the permanent default, change
# ACTIVE_MODEL in visiting_card_api/model.py instead (see HANDOVER.md).
cd "$(dirname "$0")/.." || exit 1
case "$1" in
  4b|8b) export ACTIVE_MODEL="$1" ;;
  *) echo "usage: $0 4b|8b"; exit 1 ;;
esac
mkdir -p visiting_card_api/logs
pkill -f "uvicorn app:app"
for _ in $(seq 30); do pgrep -f "llama-server.*--port 8081" >/dev/null || break; sleep 1; done
nohup caffeinate -is ./visiting_card_api/start.sh > "visiting_card_api/logs/api_$1.log" 2>&1 &
curl -sf --retry 90 --retry-delay 2 --retry-all-errors http://127.0.0.1:8000/health && echo
grep -E "Model warm|Error" "visiting_card_api/logs/api_$1.log"
