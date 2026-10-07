#!/usr/bin/env bash
# Two processes in one Space (ADR-0025): the road network on 127.0.0.1:5000 and the service on
# the port Hugging Face expects (7860). This script supervises both: when either exits, it stops
# the other and exits with the first one's status, so the Space restarts the container. TERM and
# INT (a Space restart or Ctrl-C) are forwarded to both. Needs bash 5.1+ (`wait -n` with ids).
set -uo pipefail

port="${PORT:-7860}"
echo "road network: OpenStreetMap data as of $(cat /data/mmr.osm-date 2>/dev/null || echo unknown)"

osrm-routed --algorithm mld --max-table-size 500 --port 5000 --ip 127.0.0.1 /data/mmr.osrm &
osrm_pid=$!
uvicorn gharbasket_ml.main:create_app --factory --host 0.0.0.0 --port "$port" --proxy-headers &
app_pid=$!

forward() { kill -s "$1" "$osrm_pid" "$app_pid" 2>/dev/null || true; }
trap 'forward TERM' TERM
trap 'forward INT' INT

# Returns when the first child exits, or with 128+n when a trapped signal arrives.
wait -n "$osrm_pid" "$app_pid"
status=$?
forward TERM
wait
exit "$status"
