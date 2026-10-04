#!/usr/bin/env bash
# Two processes in one Space (ADR-0025): the road network on 127.0.0.1:5000 and the service on
# the port Hugging Face expects (7860). If either dies the container exits and the Space restarts it.
set -euo pipefail

port="${PORT:-7860}"
osrm-routed --algorithm mld --max-table-size 500 --port 5000 --ip 127.0.0.1 /data/mmr.osrm &
osrm_pid=$!
trap 'kill "$osrm_pid" 2>/dev/null || true' EXIT

exec uvicorn gharbasket_ml.main:create_app --factory --host 0.0.0.0 --port "$port" --proxy-headers
