# Runbook: staging's data is wrong or lost and must be restored

Owner: Prathamesh Relekar. Last reviewed: 14 November 2026. Last drill: 14 November 2026 (see "Drill log").

This is the honest version for staging. **Staging's database has no point-in-time restore.** It is a Neon child branch,
and Neon offers point-in-time restore and snapshots only on a root branch (`scripts/neon_restore_point.py`, top
docstring). So there are only two ways back:

1. **A `before-demo-*` restore point.** `just staging-demo` makes one before it loads data. It is a copy-on-write Neon
   branch with no compute. Rolling back to it is quick and complete, but it only goes back to that moment.
2. **A dump in R2.** Once the backup workflow is merged (`backup-staging.yml`, handbook 12 §12.4), a weekly
   `pg_dump` is kept in the private bucket `gb-staging-backups`, at `staging/YYYY-MM-DD.dump`. It is restored into a
   new database, then compared with staging, not poured over it.

Anything that happened after the point you restore is gone. Staging holds synthetic and test data, so this is
a nuisance, not a loss. The pilot database is a different case: its backups are decided with Q3 (handbook 12, "Open").
Until then this runbook is the drill, not the production procedure.

## Symptoms

- A demo load, a bad migration, a bad job run or a manual SQL change left staging's data wrong, and fixing forward is
  harder than going back.
- Rows or whole tables are missing; `/v1/ready` says the database is fine but the app shows nothing.
- A drill: Rhythm Thakur and Manish Raje walk through this runbook before the pilot (handbook 12 §12.4 and §12.8).

## Checks

1. **Do you need a restore at all?** If one job wrote wrong rows, a fix-forward migration or the job's own re-run is
   cheaper (`cutoff-failed.md`, the README's "Run a job by hand"). Money rows are never edited, only added (handbook 05).
2. **Which restore points exist?** In the Neon console, project → Branches. Restore points are named
   `before-demo-<UTC time>` (for example `before-demo-20261107T0758Z`), and a past rollback leaves
   `<branch>-with-demo-<UTC time>`. The project allows 10 branches in all.
3. **Is the point before the damage?** The name is the time it was made, in UTC. Anything newer than that is lost.
4. **What is the plan for what you will lose?** Write down, before you restore:
   - the time you restore to, and the time of the damage;
   - which deletions were purged after the point (see "After a restore", point 3), and which sessions were revoked.
5. **Do you hold the tools?** `NEON_API_KEY` (an organisation key, or `NEON_PROJECT_ID` next to it) in Infisical
   `staging`. Without them the script cannot find the project.

## Common causes and fixes

| What happened | Go to |
|---|---|
| A demo load went wrong (`just staging-demo`) | Recovery A, from the restore point it printed |
| A bad migration or job damaged data, and a `before-demo-*` point is before it | Recovery A |
| The damage is older than every restore point, or you need to look at old data | Recovery B (the dump), into a new database |
| The Neon project is gone or suspended | Neon console first: a suspended compute comes back at the start of the month or on a paid plan. A deleted project is rebuilt from the latest dump (Recovery B into a new project, then a new `DATABASE_URL` in Infisical and Render) and `alembic upgrade head` |
| Valkey (rate limits, deny-list) lost | Nothing to restore (handbook 12 §12.4) |

## Recovery steps

### Recovery A: roll staging back to a restore point

1. **Stop writers.** Tell the team. Merge nothing and let no deploy run (`deploy-staging.yml` runs migrations). Do it
   outside the evening windows (README, "Run a job by hand"): the VM scheduler keeps calling jobs, and a job that runs
   during the rollback fails or is lost.
2. **Roll back.** The script asks for the host as a second confirmation, and keeps the replaced state:

   ```
   GB_ALLOW_REMOTE_SEED=<host> infisical run --env=staging -- just staging-rollback before-demo-20261107T0758Z
   ```

   `<host>` is the Neon host of `DATABASE_URL_DIRECT`; running the command without it prints the host to use. The
   database's URL stays the same. The branch keeps the replaced state as `<branch>-with-demo-<UTC time>`, and the
   restore point becomes staging's parent. **Both stay for good** (Neon cannot delete a branch with children), so a
   rollback uses up branches of the free 10. The script refuses before it starts when there is no room.
3. **Bring the code and schema in line.** If the restore point is older than the latest migration, run
   `alembic upgrade head` by redeploying: GitHub → Actions → Deploy staging → Run workflow on `main`. If it is not older,
   still redeploy once, so the API reconnects cleanly.
4. Go to "After a restore" below.

### Recovery B: restore a dump into a new database

The dump is made by `pg_dump --format=custom`. It holds all tables of the staging database, including personal data of
test users, so treat the file as personal data: download it to your own machine, restore from it, delete it.

1. **Download it.** The read-write token for the bucket is in Infisical `staging` as `BACKUP_S3_ENDPOINT_URL`,
   `BACKUP_S3_ACCESS_KEY` and `BACKUP_S3_SECRET_KEY` (the same values as the GitHub secrets). Nothing is printed:

   ```
   infisical run --env=staging -- sh -c 'AWS_ACCESS_KEY_ID="$BACKUP_S3_ACCESS_KEY" \
     AWS_SECRET_ACCESS_KEY="$BACKUP_S3_SECRET_KEY" AWS_DEFAULT_REGION=auto \
     aws s3 cp s3://gb-staging-backups/staging/2026-11-15.dump . --endpoint-url "$BACKUP_S3_ENDPOINT_URL"'
   ```

   List the keys first with `aws s3 ls s3://gb-staging-backups/staging/` in the same way. Keys are dates; the newest
   is the last.
2. **Make an empty target. Never restore into the live staging branch.** In the Neon console, make a new branch from
   staging, named `restore-check-<date>`, and in it a new empty database `restore_check`. (Or a new Neon project, if
   the old one is gone.) Copy its **direct** connection string (not the pooled one) into a shell variable without
   saving it to a file: `read -rs RESTORE_URL`.
3. **Restore.** `pg_restore` must be version 18 or newer, to read a dump made by the 18 client. If you have no local
   client, use the official image:

   ```
   docker run --rm -e RESTORE_URL -v "$PWD:/b:ro" postgres:18 \
     pg_restore --no-owner --no-acl --dbname "$RESTORE_URL" /b/2026-11-15.dump
   ```

   The dump needs the extensions the app uses (`postgis`, `vector`, `pg_trgm`, `unaccent`, `pgcrypto`), which the restore
   creates. The last line shows how many errors were ignored. Expect none; read any before you trust the result.
4. **Check it** with the queries below, on `restore_check`. Compare with the live branch if you are looking for rows.
5. **If you are replacing staging with it,** do not swap the URL casually: ask Prathamesh. The safe order is to take
   a restore point of the live branch first (`infisical run --env=staging -- uv run python
   scripts/neon_restore_point.py create before-restore`), then point `DATABASE_URL` and `DATABASE_URL_DIRECT` at the
   restored database in Infisical `staging`, in the Render service and in the GitHub secret `DATABASE_URL_DIRECT`, and
   redeploy. Run `infisical run --env=staging -- just jobs-vm env <ip> <key>` only if `JOBS_TOKEN` changed; the
   scheduler talks to the API, not to the database.
6. **Clean up.** Delete the downloaded dump from your machine. Delete the `restore-check-<date>` branch when you no longer
   need it (it counts against the 10 branches).

### Checking a dump without Neon

To prove a dump restores, without using one of Neon's 10 branches or staging's compute hours, restore it into a
throwaway container of the project's own database image (`gharbasket/postgres:18-local`, built by `just up`; it has
PostGIS and pgvector). The image's start-up script needs `POSTGRES_DB=gharbasket`, so make the empty target next to it.
If you have no AWS CLI, step 1 works with `docker run --rm -v "$PWD:/aws" -e AWS_ACCESS_KEY_ID="$BACKUP_S3_ACCESS_KEY"
-e AWS_SECRET_ACCESS_KEY="$BACKUP_S3_SECRET_KEY" -e AWS_DEFAULT_REGION=auto amazon/aws-cli:2.22.35 s3 cp …` inside the
same `infisical run … sh -c '…'`.

```
chmod 600 2026-11-11.dump
docker run -d --name gb-restore-drill -e POSTGRES_PASSWORD=drill -e POSTGRES_DB=gharbasket \
  -v "$PWD:/b:ro" gharbasket/postgres:18-local
docker exec gb-restore-drill pg_isready -U postgres          # repeat until "accepting connections"
docker exec gb-restore-drill psql -U postgres -c "create database restore_check"
docker exec gb-restore-drill pg_restore --no-owner --no-acl -U postgres --dbname restore_check /b/2026-11-11.dump
docker exec -it gb-restore-drill psql -U postgres -d restore_check   # the queries below
docker rm -f gb-restore-drill && rm 2026-11-11.dump               # always, the dump holds personal data
```

### Verification queries

Run on the restored database (Neon SQL editor, or `psql "$RESTORE_URL"`):

```sql
SELECT version_num FROM platform.alembic_version;          -- the migration head at the time of the dump
SELECT max(started_at) FROM platform.job_runs;             -- when the data stops
SELECT count(*) FROM identity.users;
SELECT count(*) FROM billing.journal_entries;
SELECT count(*) FROM identity.deletions WHERE purged_at IS NOT NULL;
-- every ledger entry balances: must be 0
SELECT count(*) FROM (SELECT entry_id FROM billing.postings GROUP BY entry_id HAVING sum(amount) <> 0) x;
```

The ledger has a deferred check that every entry's postings sum to zero (handbook 13 §13.7), so a restore cannot bring
back half an entry. A count that is far below what you expect means you restored the wrong file.

## After a restore

1. **Redeploy** so the API opens fresh connections, or run `alembic upgrade head` through the deploy workflow if the
   point is older than the latest migration.
2. **Sessions.** A session revoked after the restore point is valid again, and the Valkey deny-list keeps a revoked
   session for only about 15 minutes. People who signed out since then are signed in again; a stolen phone that was
   signed out is signed in again. There is no admin endpoint to sign another person out (`logout-all` is each user's
   own), so if sessions were revoked for a security reason, an audited statement is needed. For one user (`<user id>`
   from the audit log):

   ```sql
   UPDATE identity.sessions SET revoked_at = now(), revoke_reason = 'restore'
   WHERE user_id = '<user id>' AND revoked_at IS NULL;
   ```

   To sign out everyone, run it without the `user_id` condition: everybody signs in again. It takes effect at the next
   refresh, within 15 minutes.
3. **Erasures.** An account that was purged after the restore point comes back, with its personal data. This breaks
   a promise to the person (handbook 13 §13.6, erasure). Before you restore, list the rows with `purged_at` after the
   point in `identity.deletions` on the live database and keep the user ids. After the restore, set those users' deletions
   to be purged again and run `purge_deleted_users` by hand (README, "Run a job by hand"). The same goes for a data
   export a person had already deleted.
4. **Re-run the day's jobs.** Jobs that ran after the point have to run again. Find them with `just job-runs`: runs
   after the point are in the old database only. Run today's missed ones with their `scheduled_for`, in the order in the
   README. Check `build_procurement_plan`, `assign_kitchens` and `solve_routes` after a late cut-off
   ([`cutoff-failed.md`](cutoff-failed.md)).
5. **Money.** Wallet top-ups and refunds after the point exist at Razorpay but not in the restored ledger. On staging
   Razorpay is in test mode, so nothing real is lost. Run `reconcile_payments` (`razorpay-down.md`), which opens an item
   for each payment it cannot match; resolve them with a note.
6. **Files in R2** are not restored. A file the restored rows do not mention is cleaned by `sweep_uploads`; a row that
   points at a file deleted after the point shows a missing file. For staging that is accepted.
7. **Tell the team** what time you restored to and what was lost, in the team channel.

## What not to do

- Do not restore into the live staging branch from a dump. Restore into a new database and compare.
- Do not roll back to a restore point while a demo load or a deploy is running.
- Do not delete `before-demo-*` branches that a rollback made the parent of. Neon refuses, and the 10-branch limit is real.
- Do not paste a connection string, `NEON_API_KEY` or the backup token into chat, an issue or a PR. Use `infisical run`
  and `read -rs`.
- Do not keep a downloaded dump after the restore. It holds personal data.
- Do not tell anyone staging has point-in-time restore. It does not.

## Drill log

| Date | Who | Dump | Into | Result |
|---|---|---|---|---|
| 14 Nov 2026 | Prathamesh Relekar (with Claude Code) | `staging/2026-11-11.dump` (1.5 MB, the first, manual run of `backup-staging.yml`) | A local `gharbasket/postgres:18-local` container ("Checking a dump without Neon") | `pg_restore` exit 0, no errors, in seconds. Head `8b2e5d4c7a19` (#519), 14 schemas, extensions pg_trgm, pgcrypto, postgis, unaccent, vector. 30 users, 147 orders, 286 journal entries (572 postings, all balanced), last job run 11 Nov 16:47 UTC. Counts equal live staging's rows created before the dump. Container and file deleted after. Not yet drilled: Recovery A, and Recovery B into a Neon branch with the API pointed at it |

Rhythm Thakur and Manish Raje run the next drill before the pilot (handbook 12 §12.8), from a scheduled Sunday dump.

## Related

Handbook 12 §12.3 (Neon, staging demo data) and §12.4 (backups), handbook 13 §13.5 and §13.6,
`scripts/neon_restore_point.py`, `scripts/staging-demo.sh`, `.github/workflows/backup-staging.yml`,
[`deploy-rollback.md`](deploy-rollback.md), [`rotate-secrets.md`](rotate-secrets.md), [`README.md`](README.md).
