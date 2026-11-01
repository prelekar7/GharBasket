#!/usr/bin/env bash
# Run the daily mandi price collector (backend/training jobs/collect_mandi) from a Mac in India.
#
# api.data.gov.in refuses connections from outside India (GitHub's runners: TCP refused in 0.5 s,
# with or without a browser User-Agent; checked 1 Nov 2026), so the Actions schedule can't collect.
# The data.gov.in resource keeps only today's prices, so a missed day is lost history.
#
#   scripts/mandi-on-mac.sh install    # a launchd job at 18:45 IST daily (a missed run runs on wake)
#   scripts/mandi-on-mac.sh run        # collect now (what the job runs)
#   scripts/mandi-on-mac.sh status     # the job and the last lines of its log
#   scripts/mandi-on-mac.sh uninstall
#
# Secrets come from Infisical `staging`: DATA_GOV_IN_API_KEY, JOBS_TOKEN, S3_ENDPOINT_URL and an R2
# token that can write gb-staging-lake: TRAINING_S3_ACCESS_KEY / TRAINING_S3_SECRET_KEY when set (the
# training jobs' own token), else the API's S3_ACCESS_KEY / S3_SECRET_KEY. The Mac must be logged in
# to Infisical (`infisical login`). Logs: ~/Library/Logs/gharbasket-mandi.log.
set -euo pipefail

repo="$(cd "$(dirname "$0")/.." && pwd)"
label="in.gharbasket.mandi"
plist="$HOME/Library/LaunchAgents/$label.plist"
log="$HOME/Library/Logs/gharbasket-mandi.log"

collect() {
  cd "$repo"
  # launchd starts with a bare PATH; the tools live in Homebrew and ~/.local/bin.
  export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"
  echo "== $(date '+%Y-%m-%d %H:%M:%S %Z') collect_mandi"
  # Single quotes: the variables are expanded inside infisical's environment, not here.
  infisical run --env=staging --silent -- sh -c '
    exec env S3_ACCESS_KEY="${TRAINING_S3_ACCESS_KEY:-$S3_ACCESS_KEY}" \
      S3_SECRET_KEY="${TRAINING_S3_SECRET_KEY:-$S3_SECRET_KEY}" \
      API_URL="${STAGING_API_URL:-https://gharbasket-api.onrender.com}" \
      S3_REGION=auto S3_BUCKET_LAKE=gb-staging-lake S3_BUCKET_MLFLOW=gb-staging-mlflow \
      uv run --directory backend/training python -m gharbasket_training.jobs.collect_mandi \
      --push-index'
}

case "${1:-}" in
  run)
    collect
    ;;
  install)
    mkdir -p "$(dirname "$plist")" "$(dirname "$log")"
    cat >"$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key>
  <array><string>$repo/scripts/mandi-on-mac.sh</string><string>run</string></array>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>18</integer><key>Minute</key><integer>45</integer></dict>
  <key>StandardOutPath</key><string>$log</string>
  <key>StandardErrorPath</key><string>$log</string>
</dict>
</plist>
EOF
    launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$plist"
    echo "Installed: collect_mandi runs daily at 18:45 (Mac time; a run missed while asleep runs on wake)."
    echo "Log: $log"
    ;;
  uninstall)
    launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
    rm -f "$plist"
    echo "Removed $label"
    ;;
  status)
    launchctl print "gui/$(id -u)/$label" 2>/dev/null | grep -E "state|last exit code|runs" || echo "$label is not installed"
    tail -n 5 "$log" 2>/dev/null || true
    ;;
  *)
    echo "usage: $0 install | run | status | uninstall" >&2
    exit 2
    ;;
esac
