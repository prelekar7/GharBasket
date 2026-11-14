#!/usr/bin/env bash
# The operator's side of the MS-A demo (docs/demo/ms-a.md): runs one demo week's evening jobs on
# staging ahead of the clock, through the normal job endpoints with `scheduled_for` (business rules
# use it, ADR-0021), so the week fits in one sitting. Run every step under Infisical staging:
#
#   infisical run --env=staging -- just demo-ms-a check     # health, token, the demo's delivery day T
#   just demo-ms-a day                                      # only T and the job times (no token)
#   infisical run --env=staging -- just demo-ms-a plan      # T-3 18:00 generate_weekly_plans
#   infisical run --env=staging -- just demo-ms-a announce  # T-3 18:30 send_box_ready_notices
#   infisical run --env=staging -- just demo-ms-a cutoff    # T-1 21:00 cutoff_lock
#   infisical run --env=staging -- just demo-ms-a ledger +91…   # the customer's ledger, read only
#
# Other steps: `reconcile` (reconcile_payments now: a top-up whose webhook is slow), `grace`
# (T-1 23:00 payment_grace_expiry), `sweep` (outbox_sweep now), `runs` (today's job runs).
# Each job step runs outbox_sweep after it, so the events it published reach their consumers.
#
# T is the demo society's next delivery day whose cut-off (T-1 21:00 IST) has not passed. The
# society is DEMO_SOCIETY (default sunrise-towers-kharghar, live in the seed); DEMO_DAY=YYYY-MM-DD
# pins T. Customer edits use the real clock, so run `cutoff` only after the customer has approved.
# The real scheduler on the VM still runs the same jobs at their real times; they are idempotent
# (a box is drafted once, a locked order is counted as already locked).
set -euo pipefail
cd "$(dirname "$0")/.."

society="${DEMO_SOCIETY:-sunrise-towers-kharghar}"
api="${STAGING_API_URL:-https://api.gharbasket.co.in}"  # the staging API (ADR-0035)

usage() {
  sed -n '2,21p' "$0" | sed 's/^# \{0,1\}//'
  exit 2
}

need_api() {
  if [ -z "$api" ]; then
    echo "STAGING_API_URL is empty" >&2
    exit 1
  fi
  api="${api%/}"
}

need_token() {
  if [ -z "${JOBS_TOKEN:-}" ]; then
    echo "JOBS_TOKEN is not set: run under infisical run --env=staging -- …" >&2
    exit 1
  fi
}

# The delivery day T (YYYY-MM-DD) from the society's public page, or DEMO_DAY.
delivery_day() {
  if [ -n "${DEMO_DAY:-}" ]; then
    echo "$DEMO_DAY"
    return
  fi
  local page
  page=$(curl --silent --show-error --fail --max-time 60 \
    -H "accept: application/json" "$api/v1/public/societies/$society") || {
    echo "could not read $api/v1/public/societies/$society" >&2
    exit 1
  }
  python3 -c '
import json, sys
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo
IST = ZoneInfo("Asia/Kolkata")
page = json.loads(sys.argv[1])
names = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
days = {names.index(d) for d in page.get("delivery_days") or []}
if page.get("status") != "live" or not days:
    slug, status = page.get("slug"), page.get("status")
    sys.exit(f"{slug} is {status} with no delivery days: pick a live society (DEMO_SOCIETY)")
now = datetime.now(IST)
day = now.date() + timedelta(days=1)
while True:
    cutoff = datetime.combine(day - timedelta(days=1), time(21, 0), IST)
    if day.weekday() in days and now < cutoff:
        print(day.isoformat())
        break
    day += timedelta(days=1)
' "$page"
}

# An IST wall time relative to T as UTC ISO: at <T> <days before T> <HH:MM>.
at() {
  python3 -c '
import sys
from datetime import date, datetime, time, timedelta, UTC
from zoneinfo import ZoneInfo
day = date.fromisoformat(sys.argv[1]) - timedelta(days=int(sys.argv[2]))
hh, mm = map(int, sys.argv[3].split(":"))
print(datetime.combine(day, time(hh, mm), ZoneInfo("Asia/Kolkata")).astimezone(UTC).isoformat().replace("+00:00", "Z"))
' "$1" "$2" "$3"
}

# Run one job; prints its status and summary. $2 is the scheduled_for (UTC ISO), or empty for now.
job() {
  local name="$1" when="${2:-}" body='{}' out
  [ -n "$when" ] && body="{\"scheduled_for\": \"$when\"}"
  echo "→ $name${when:+ for $when}"
  if ! out=$(curl --silent --show-error --fail-with-body --max-time 300 \
    -X POST "$api/v1/internal/jobs/$name" \
    -H "Authorization: Bearer $JOBS_TOKEN" -H "Content-Type: application/json" -d "$body"); then
    echo "  failed: $out" >&2
    return 1
  fi
  python3 -c '
import json, sys
run = json.loads(sys.argv[1])
status, summary, error = run.get("status"), run.get("summary"), run.get("error")
print(f"  {status}: {json.dumps(summary, ensure_ascii=False)}")
if error:
    print(f"  error: {error}")
sys.exit(0 if run.get("status") == "ok" else 1)
' "$out"
}

step() {
  local name="$1" before="$2" clock="$3" day
  need_api
  need_token
  day=$(delivery_day)
  echo "T = $day ($society)"
  job "$name" "$(at "$day" "$before" "$clock")"
  job outbox_sweep "" >/dev/null && echo "  outbox swept"
}

cmd="${1:-}"
[ $# -gt 0 ] && shift
case "$cmd" in
  check)
    need_api
    echo "API: $api"
    curl --silent --show-error --fail --max-time 90 "$api/v1/health"
    echo
    need_token
    curl --silent --show-error --fail --max-time 30 -o /dev/null \
      -H "Authorization: Bearer $JOBS_TOKEN" "$api/v1/internal/jobs" && echo "Jobs token: accepted"
    "$0" day
    ;;
  day)
    need_api
    day=$(delivery_day)
    echo "Demo society: $society"
    echo "T (delivery day): $day"
    echo "  plan      T-3 18:00 IST = $(at "$day" 3 18:00)"
    echo "  announce  T-3 18:30 IST = $(at "$day" 3 18:30)"
    echo "  cutoff    T-1 21:00 IST = $(at "$day" 1 21:00)  (the real cut-off for edits)"
    echo "  grace     T-1 23:00 IST = $(at "$day" 1 23:00)"
    ;;
  plan) step generate_weekly_plans 3 18:00 ;;
  announce) step send_box_ready_notices 3 18:30 ;;
  cutoff) step cutoff_lock 1 21:00 ;;
  grace) step payment_grace_expiry 1 23:00 ;;
  reconcile)
    need_api
    need_token
    job reconcile_payments ""
    job outbox_sweep "" >/dev/null && echo "  outbox swept"
    ;;
  sweep)
    need_api
    need_token
    job outbox_sweep ""
    ;;
  runs)
    need_api
    need_token
    curl --silent --show-error --fail --max-time 30 \
      -H "Authorization: Bearer $JOBS_TOKEN" "$api/v1/internal/jobs/runs?limit=15" |
      python3 -c '
import json, sys
body = json.load(sys.stdin)
for run in body if isinstance(body, list) else body.get("items", []):
    print(run.get("started_at", "")[:19], run.get("name"), run.get("status"), run.get("scheduled_for", "")[:16])
'
    ;;
  ledger)
    [ $# -ge 1 ] || {
      echo "usage: demo-ms-a ledger +91XXXXXXXXXX" >&2
      exit 2
    }
    cd backend/api
    exec uv run --frozen python -m gharbasket.tools.demo_ledger "$@"
    ;;
  -h | --help | help | "") usage ;;
  *)
    echo "unknown step: $cmd" >&2
    usage
    ;;
esac
