#!/usr/bin/env bash
# Smoke test a deployed API (staging): `just cloud <url>`, or the deploy workflow after a deploy.
# Needs JOBS_TOKEN for /v1/ready (`infisical run --env=staging -- just cloud <url>`).
set -euo pipefail

url="${1:?usage: smoke-staging.sh <api base url>}"
url="${url%/}"
: "${JOBS_TOKEN:?set JOBS_TOKEN (the staging operator token)}"
failures=0

check() { # check <description> <expected> <actual>
  if [ "$2" = "$3" ]; then
    echo "  ok    $1"
  else
    echo "  FAIL  $1: expected '$2', got '$3'"
    failures=$((failures + 1))
  fi
}

# status <curl args...>: the HTTP status code only.
status() { curl --silent --max-time 60 --output /dev/null --write-out '%{http_code}' "$@"; }
# field <json> <python expression on d>: one value from a JSON body.
field() { python3 -c "import json,sys; d=json.loads(sys.argv[1]); print($2)" "$1" 2>/dev/null || echo "?"; }

echo "Smoke test of $url"

health=$(curl --silent --max-time 60 "$url/v1/health")
check "/v1/health answers ok" "ok" "$(field "$health" 'd["status"]')"
echo "        environment $(field "$health" 'd["environment"]'), commit $(field "$health" 'd.get("commit")')"

check "/v1/ready refuses a missing operator token" "401" "$(status "$url/v1/ready")"
ready=$(curl --silent --max-time 60 -H "Authorization: Bearer $JOBS_TOKEN" "$url/v1/ready")
check "/v1/ready: database" "ok" "$(field "$ready" 'd["checks"]["database"]')"
check "/v1/ready: valkey" "ok" "$(field "$ready" 'd["checks"]["valkey"]')"

dev_login=$(curl --silent --max-time 60 -H 'content-type: application/json' \
  -d '{"dev_phone":"+919800001001","device":{"platform":"android"}}' "$url/v1/auth/exchange")
check "dev login is refused outside local dev" "auth.dev_login_disabled" "$(field "$dev_login" 'd["code"]')"

me=$(curl --silent --max-time 60 "$url/v1/me")
check "/v1/me needs sign-in" "auth.invalid_token" "$(field "$me" 'd["code"]')"

problem_type=$(curl --silent --max-time 60 --output /dev/null --write-out '%{content_type}' "$url/v1/nope")
check "errors are problem+json" "application/problem+json" "$problem_type"

check "the API contract is published" "200" "$(status "$url/v1/openapi.json")"

if [ "$failures" -gt 0 ]; then
  echo "$failures check(s) failed"
  exit 1
fi
echo "All checks passed"
