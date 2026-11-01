#!/usr/bin/env bash
# Create (or re-key) the training jobs' read-only Postgres role and store its URL as the GitHub
# secret DATABASE_URL_RO, without printing it (ADR-0025, handbook 12 one-time setup).
#
#   infisical run --env=staging -- scripts/neon-ro-role.sh            # staging (Neon)
#   DATABASE_URL=postgresql://... scripts/neon-ro-role.sh --no-secret  # try it on a local database
#
# Runs scripts/neon-ro-role.sql as the owner (DATABASE_URL_DIRECT, else DATABASE_URL), then checks
# the new role can read households and cannot write, then sets the secret. A new random password
# each run, so running it again rotates the key.
set -euo pipefail
cd "$(dirname "$0")/.."

owner="${DATABASE_URL_DIRECT:-${DATABASE_URL:?set DATABASE_URL_DIRECT (the Neon owner, direct)}}"
role="${RO_ROLE:-gharbasket_ro}"
password=$(openssl rand -hex 24)

psql "$owner" -X -q -v ON_ERROR_STOP=1 -v role="$role" -v password="$password" \
  -f scripts/neon-ro-role.sql

# The owner's URL with the role's name and password (host, database and sslmode unchanged).
ro_url=$(python3 -c '
import sys, urllib.parse as u
url, role, password = sys.argv[1:4]
parts = u.urlsplit(url)
host = parts.netloc.rsplit("@", 1)[-1]
print(u.urlunsplit(parts._replace(netloc=f"{u.quote(role)}:{password}@{host}")), end="")
' "$owner" "$role" "$password")

households=$(psql "$ro_url" -X -qtA -c "select count(*) from identity.households")
echo "  ok    $role reads identity.households ($households rows)"
if psql "$ro_url" -X -qtA -c "create table catalog.ro_probe (x int)" >/dev/null 2>&1; then
  psql "$owner" -X -q -c "drop table catalog.ro_probe"
  echo "  FAIL  $role could create a table" >&2
  exit 1
fi
echo "  ok    $role cannot write"
if psql "$ro_url" -X -qtA -c "select 1 from identity.users limit 1" >/dev/null 2>&1; then
  echo "  FAIL  $role can read identity.users" >&2
  exit 1
fi
echo "  ok    $role cannot read identity.users"

if [ "${1:-}" = "--no-secret" ]; then
  echo "Role ready; DATABASE_URL_RO not set (--no-secret)"
else
  printf %s "$ro_url" | gh secret set DATABASE_URL_RO
  echo "GitHub secret DATABASE_URL_RO set for role $role"
fi
