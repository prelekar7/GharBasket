#!/usr/bin/env bash
# Sign in as every seed person against a running API and check /v1/me against the seed.
#
#   scripts/real-check.sh [PORT | BASE_URL]      default http://localhost:8000
#
# For each phone in tools/seed.py PEOPLE: POST /v1/auth/exchange {dev_phone} the way the web apps do
# (X-GB-Client: web, Origin: http://localhost:5173, cookie jar), then GET /v1/me with the cookie, and
# compare the roles and scope ids with the seed. One line per person; exit 1 on any mismatch.
# The API must run with APP_ENV=dev on a database that has been seeded (`just seed`). A run uses 13 of the
# 30 dev sign-ins an IP gets per 10 minutes (identity/router.py EXCHANGE_PER_IP).
set -uo pipefail
cd "$(dirname "$0")/.."

target=${1:-8000}
case "$target" in
  http*) base=${target%/} ;;
  *)     base="http://localhost:$target" ;;
esac
origin=http://localhost:5173

work=$(mktemp -d "${TMPDIR:-/tmp}/real-check.XXXXXX")
trap 'rm -rf "$work"' EXIT

curl -s -m 5 -o /dev/null "$base/v1/health" || { echo "real-check: no API answering at $base" >&2; exit 1; }

phones=$(uv run python -c 'from gharbasket.tools.seed import PEOPLE; print(*(p.phone for p in PEOPLE))') \
  || { echo "real-check: could not read the seed people" >&2; exit 1; }

for phone in $phones; do
  jar="$work/$phone.jar"
  : >"$work/$phone.exchange.code"
  curl -s -m 15 -o "$work/$phone.exchange.json" -w '%{http_code}' -c "$jar" -b "$jar" \
    -X POST "$base/v1/auth/exchange" \
    -H 'content-type: application/json' -H 'X-GB-Client: web' -H "Origin: $origin" \
    -d "{\"dev_phone\": \"$phone\", \"device\": {\"platform\": \"web\"}}" >"$work/$phone.exchange.code"
  curl -s -m 15 -o "$work/$phone.me.json" -w '%{http_code}' -b "$jar" \
    "$base/v1/me" -H 'X-GB-Client: web' -H "Origin: $origin" >"$work/$phone.me.code"
done

uv run python - "$work" <<'PY'
import json
import sys
from pathlib import Path

from gharbasket.tools.seed import CAPTAINS, PEOPLE

work = Path(sys.argv[1])
captain_of = {phone: str(society) for phone, society, _ in CAPTAINS}
bad = 0
for p in PEOPLE:
    expected = {("customer", None)} | {(role, str(scope) if scope else None) for role, scope in p.roles}
    if p.phone in captain_of:
        expected.add(("captain", captain_of[p.phone]))
    problems = []
    got = set()
    code = (work / f"{p.phone}.exchange.code").read_text().strip()
    me_code = (work / f"{p.phone}.me.code").read_text().strip() if (work / f"{p.phone}.me.code").exists() else ""
    if code != "200":
        problems.append("exchange rate-limited (30 sign-ins per 10 minutes per IP; wait, or flush the Valkey db)" if code == "429" else f"exchange answered {code or 'nothing'}")
    elif me_code != "200":
        problems.append(f"/v1/me answered {me_code or 'nothing'}")
    else:
        me = json.loads((work / f"{p.phone}.me.json").read_text())
        if me.get("phone_e164") != p.phone:
            problems.append(f"phone {me.get('phone_e164')}")
        if me.get("name") != p.name:
            problems.append(f"name {me.get('name')!r}")
        for r in me.get("roles", []):
            got.add((r["role"], r.get("scope_id")))
            if r.get("status") != "verified":
                problems.append(f"{r['role']} is {r.get('status')}")
        if missing := expected - got:
            problems.append("missing " + ", ".join(f"{r}@{(s or '-')[-4:]}" for r, s in sorted(missing, key=str)))
        if extra := got - expected:
            problems.append("unexpected " + ", ".join(f"{r}@{(s or '-')[-4:]}" for r, s in sorted(extra, key=str)))
    roles = ", ".join(sorted(r for r, _ in expected))
    if problems:
        bad += 1
    print(f"{'FAIL' if problems else 'ok  '} {p.phone}  {p.name:<16} {roles:<26}{'  ' + '; '.join(problems) if problems else ''}")
print(f"{len(PEOPLE) - bad}/{len(PEOPLE)} match the seed")
sys.exit(1 if bad else 0)
PY
