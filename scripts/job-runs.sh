#!/usr/bin/env bash
# Recent job runs, newest first, from GET /v1/internal/jobs/runs (M0 §3, ADR-0033).
#   scripts/job-runs.sh [name] [status] [limit]                    # the local API (localhost:8000)
#   infisical run --env=staging -- scripts/job-runs.sh cutoff_lock   # staging, with JOBS_TOKEN
# The token goes to curl on stdin, never on a command line.
set -euo pipefail

api="${STAGING_API_URL:-http://localhost:8000}"
name="${1:-}"
status="${2:-}"
limit="${3:-20}"
query=(--data-urlencode "limit=$limit")
[ -z "$name" ] || query+=(--data-urlencode "name=$name")
[ -z "$status" ] || query+=(--data-urlencode "status=$status")

printf 'Authorization: Bearer %s\n' "${JOBS_TOKEN:-}" |
  curl --silent --show-error --fail-with-body --max-time 120 -G -H @- \
    "${query[@]}" "$api/v1/internal/jobs/runs" |
  python3 -c '
import json, sys
from datetime import datetime
from zoneinfo import ZoneInfo

ist = ZoneInfo("Asia/Kolkata")
for run in json.load(sys.stdin):
    started = datetime.fromisoformat(run["started_at"]).astimezone(ist)
    planned = datetime.fromisoformat(run["scheduled_for"]).astimezone(ist)
    name, status = run["name"], run["status"]
    detail = run["error"] or json.dumps(run["summary"], separators=(",", ":"))
    print(f"{started:%m-%d %H:%M} IST  {name:<28} {status:<7} for {planned:%m-%d %H:%M}  {detail[:160]}")
'
