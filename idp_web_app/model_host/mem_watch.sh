#!/bin/bash
# Log the model server's memory every 2 s while a benchmark runs, on the model host (mini3).
#
#   ./mem_watch.sh qwen3-vl-4b_np4.csv      (Ctrl+C to stop)
#
# Columns: time, llama-server resident memory (MB), free system memory (MB), swap used (MB).
# Swap rising during a test means the model and its context no longer fit in memory.
OUT="${1:-mem.csv}"
PAGE=$(sysctl -n hw.pagesize)
echo "time,llama_rss_mb,free_mb,swap_used_mb" > "$OUT"
echo "Writing $OUT (Ctrl+C to stop)"
while true; do
  rss=$(ps -axo rss=,comm= | awk '/llama-server/ {s += $1} END {printf "%d", s / 1024}')
  free=$(vm_stat | awk -v p="$PAGE" '/Pages free/ {gsub("\\.", "", $3); printf "%d", $3 * p / 1048576}')
  swap=$(sysctl -n vm.swapusage | awk '{gsub("M", "", $6); printf "%d", $6}')
  echo "$(date +%H:%M:%S),$rss,$free,$swap" >> "$OUT"
  sleep 2
done
