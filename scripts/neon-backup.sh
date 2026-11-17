#!/usr/bin/env bash
# Dump a Neon database to a private R2 bucket and keep the newest KEEP dumps (handbook 12 §12.4,
# runbook docs/runbooks/restore-from-backup.md). Used by backup-staging.yml and backup-prod.yml,
# which differ only in the values below.
#
# Environment:
#   DATABASE_URL_DIRECT      the database's direct URL (the app's postgresql+asyncpg:// form works)
#   BACKUP_S3_ENDPOINT_URL   R2's S3 endpoint
#   BACKUP_S3_ACCESS_KEY     an R2 token with Object Read & Write on BACKUP_BUCKET only
#   BACKUP_S3_SECRET_KEY
#   BACKUP_BUCKET            e.g. gb-staging-backups
#   BACKUP_PREFIX            e.g. staging/ (keys are <prefix>YYYY-MM-DD.dump, the IST date)
#   KEEP                     how many dumps of that form to keep
#   PG_IMAGE                 default postgres:18; its major version must not be older than Neon's
#
# Needs docker and the AWS CLI (both on GitHub's ubuntu runners). It never prints the URL or a key.
# The dump holds the personal data of every user of that database: it is written mode 600 and
# deleted when the script ends, whatever happens.
set -euo pipefail

for name in DATABASE_URL_DIRECT BACKUP_S3_ENDPOINT_URL BACKUP_S3_ACCESS_KEY BACKUP_S3_SECRET_KEY \
  BACKUP_BUCKET BACKUP_PREFIX KEEP; do
  [ -n "${!name:-}" ] || { echo "::error::$name is not set" >&2; exit 2; }
done
case "$KEEP" in '' | *[!0-9]*) echo "::error::KEEP must be a number" >&2; exit 2 ;; esac
PG_IMAGE="${PG_IMAGE:-postgres:18}"

# libpq wants postgresql:// and sslmode=; the app's form (postgresql+asyncpg://, ssl=) is accepted.
DATABASE_URL_DIRECT=$(printf '%s' "$DATABASE_URL_DIRECT" |
  sed -E 's#^postgres(ql)?\+[a-z0-9]+://#postgresql://#; s/([?&])ssl=/\1sslmode=/')
export DATABASE_URL_DIRECT

export AWS_ACCESS_KEY_ID="$BACKUP_S3_ACCESS_KEY" AWS_SECRET_ACCESS_KEY="$BACKUP_S3_SECRET_KEY"
export AWS_DEFAULT_REGION=auto # R2 ignores the region, the CLI needs one
# Newer AWS CLI v2 versions add a checksum to every upload by default; ask for one only when needed.
export AWS_REQUEST_CHECKSUM_CALCULATION=when_required AWS_RESPONSE_CHECKSUM_VALIDATION=when_required
s3() { aws "$@" --endpoint-url "$BACKUP_S3_ENDPOINT_URL"; }

umask 077
dir=$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/backup.XXXXXX")
trap 'rm -rf "$dir"' EXIT

# A suspended Neon compute answers the first connection after a few seconds.
for attempt in $(seq 1 6); do
  if docker run --rm -e DATABASE_URL_DIRECT --entrypoint psql "$PG_IMAGE" \
    "$DATABASE_URL_DIRECT" -X -qtA -c "select 1" > /dev/null; then
    echo "The database answers"
    break
  fi
  [ "$attempt" -lt 6 ] || { echo "::error::The database did not answer in a minute" >&2; exit 1; }
  echo "The database did not answer (try $attempt of 6)"
  sleep 10
done

stamp=$(TZ=Asia/Kolkata date +%F)
file="$dir/$stamp.dump"
key="${BACKUP_PREFIX}${stamp}.dump"
# --no-owner and --no-acl: the dump restores into any database, whoever owns it there. The file is
# written by this shell, not the container, so umask 077 makes it ours alone.
docker run --rm -e DATABASE_URL_DIRECT --entrypoint pg_dump "$PG_IMAGE" \
  --format=custom --no-owner --no-acl --dbname "$DATABASE_URL_DIRECT" > "$file"
size=$(wc -c < "$file" | tr -d ' ')
echo "Dump $stamp.dump: $size bytes"

# The dump must be readable, and must hold our schemas, not an empty database.
docker run --rm -v "$dir:/b:ro" --entrypoint pg_restore "$PG_IMAGE" \
  --list "/b/$stamp.dump" > "$dir/toc.txt"
grep -qE 'TABLE identity users( |$)' "$dir/toc.txt" || {
  echo "::error::The dump has no identity.users table; not uploading it" >&2
  exit 1
}
echo "Table of contents: $(wc -l < "$dir/toc.txt" | tr -d ' ') lines"

aws --version
s3 s3 cp "$file" "s3://$BACKUP_BUCKET/$key" --no-progress
# The object must be there with the size we sent.
remote=$(s3 s3api head-object --bucket "$BACKUP_BUCKET" --key "$key" \
  --query ContentLength --output text)
if [ "$remote" != "$size" ]; then
  echo "::error::R2 holds $remote bytes of $key, expected $size" >&2
  exit 1
fi
echo "Uploaded $key ($size bytes)"

# Only keys of the form <prefix>YYYY-MM-DD.dump count; anything else in the bucket is left alone.
# The dates sort as text, so the oldest come first. An empty prefix answers null; grep then finds
# nothing, which is not an error here.
s3 s3api list-objects-v2 --bucket "$BACKUP_BUCKET" --prefix "$BACKUP_PREFIX" \
  --query 'Contents[].Key' --output json | jq -r '.[]?' |
  { grep -E "^${BACKUP_PREFIX}[0-9]{4}-[0-9]{2}-[0-9]{2}\.dump$" || true; } | sort > "$dir/keys.txt"
total=$(wc -l < "$dir/keys.txt" | tr -d ' ')
echo "$total dump(s) under $BACKUP_PREFIX, keeping the newest $KEEP"
if [ "$total" -gt "$KEEP" ]; then
  head -n "$((total - KEEP))" "$dir/keys.txt" | while read -r old; do
    echo "Deleting $old"
    s3 s3api delete-object --bucket "$BACKUP_BUCKET" --key "$old" < /dev/null # not the list
  done
fi
