# Runbook: a deploy broke staging, or the API does not answer

Owner: Prathamesh Relekar. Last reviewed: 11 November 2026.

Code can go back; the database cannot. Render keeps earlier deploys of `gharbasket-api` and can run one again. But
migrations are forward-only: `migrations/env.py` refuses any downgrade of a shared database
(`_refuse_remote_downgrade`). So a mistake in a migration is fixed with a new migration, never by undoing the old one.
This is safe because migrations are written expand-then-contract (handbook 12 §12.2): the previous code keeps working
on the newer schema.

## Symptoms

- **UptimeRobot** mails that `/v1/health` is down. It checks every 5 minutes, so the alert comes 5 to 10 minutes after
  the API stopped.
- **`deploy-staging.yml` is red** on `main`. Its log says which step failed: the migration, the deploy hook, the wait
  for the new commit (20 minutes), or the smoke test.
- The console or the app shows errors right after a deploy, or `/v1/health` reports a `commit` that is not the one you
  expect.
- Several `ops` alerts at once after a deploy. Look at the deploy first.

## Checks

1. **Is it only asleep?** The free API sleeps after 15 idle minutes and takes about a minute to wake. UptimeRobot's
   5-minute ping normally keeps it awake. `curl -sS -m 90 https://api.gharbasket.co.in/v1/health` wakes it. If it
   answers `{"status":"ok",...}` with the expected `commit`, there is nothing to roll back.
2. **Which commit runs?** The same call shows `commit`. Compare it with `git log -1 origin/main`. If they differ, a deploy
   did not finish or failed.
3. **GitHub → Actions → Deploy staging**, the latest run. Find the first red step:
   - *Run migrations on Neon* failed: the new code was never deployed (the hook comes after the migration), so staging
     still runs the old code. See "A migration failed" below.
   - *Ask Render to deploy* or *Wait until staging runs this commit* failed: the migration is already on Neon. Read the
     Render deploy log (next point).
   - *Smoke test* failed: the new code is live and a check failed. Its log names the check.
4. **Render dashboard**, service `gharbasket-api`: Events and Logs. A failed build, a crash loop (look for a settings
   error: a missing secret stops the start: `platform/settings.py` refuses to run without them) or an out-of-memory kill each show here. Is the workspace out of its
   750 free instance hours? Both free web services share them.
5. **Neon**: is the project suspended (compute hours used up, handbook 12 §12.3)? Then `/v1/ready` fails on the database
   while `/v1/health` still answers. That is not a code problem; ADR-0020 has the limits.

## Common causes and fixes

| Cause | How to tell | Fix |
|---|---|---|
| **Sleeping free instance** | Slow first call, then ok | Nothing |
| **Bad code** (a crash, a wrong answer) | Started with a deploy; the Render log shows the error | Roll back (Recovery 1), then fix forward on a branch |
| **A migration failed** | *Run migrations on Neon* is red | Staging runs the old code, which is fine. Fix the migration in a new PR. All pending migrations run in one transaction, so a failed one leaves nothing half-applied; run `alembic current` against Neon to be sure |
| **A migration succeeded and the new code is wrong** | Migration green, smoke red | Roll back the code. The new schema stays; the old code works on it because the change was an addition |
| **A missing or wrong secret** | The service restarts again and again; the log names the setting | Put the value in Infisical `staging` and on Render, then redeploy (`rotate-secrets.md`) |
| **Out of instance hours, or Neon suspended** | Render shows "suspended", or `/v1/ready` fails on the database | Wait for the month to roll over, or stop what is not needed. Paid plans are a decision for before the pilot (Q3) |
| **Render itself is down** | status.render.com | Wait. Nothing of ours is lost |

## Recovery steps

1. **Roll the code back.** Render dashboard → `gharbasket-api` → Events → find the last good deploy → Rollback. It starts
   that earlier build again in about a minute. Render turns off auto-deploy on a dashboard rollback; for the API it is
   already off (`autoDeployTrigger: "off"` in `render.yaml`), so nothing else changes. For `gharbasket-ml`, which
   deploys on commit, turn auto-deploy back on in Settings once the fix is merged.
2. **Check it.** Wait for `/v1/health` to report the old commit, then run the smoke test:

   ```
   infisical run --env=staging -- just cloud https://api.gharbasket.co.in
   ```

   It exits 1 if a check fails. It needs `JOBS_TOKEN`, which `infisical run` supplies.
3. **Stop the bad code coming back.** The next green CI on `main` runs `deploy-staging.yml` and deploys the newest
   commit, bad code included. So either pause merging to `main`, or merge the revert first: open a `fix/` branch with
   `git revert <commit>` (or the real fix) and a test. Deploying that is the proper end of the incident.
4. **Fix a schema mistake forward.** Write a new migration (`YYYYMMDD_HHMM_<module>_<desc>`) that corrects it, keeping
   a single Alembic head. The deploy workflow applies it.
5. **Catch up on what the outage missed.** `just job-runs "" failed 50` lists failed runs. Jobs missed while the API was
   down are run by hand with their `scheduled_for` (README, "Run a job by hand"). If the outage covered 21:00 IST, start
   with [`cutoff-failed.md`](cutoff-failed.md).
6. Add a line to this runbook if the cause was new.

## What not to do

- Do not run `alembic downgrade` on Neon. It is refused, and bypassing the guard on a shared database is how data is lost.
- Do not re-run `deploy-staging.yml` by hand on an older ref ("Run workflow" with an old branch or tag). Its
  `alembic upgrade head` would meet a database that is already at a newer revision and fail, and a failed run is
  a second problem.
- Do not edit schema or data by hand in Neon's SQL editor to "get it working". Money rows are never updated, only added
  (handbook 05).
- Do not deploy a commit by hand with the deploy hook while a newer migration is pending: the hook skips the migration
  step. Let the workflow do both.
- Do not paste the deploy hook URL or `JOBS_TOKEN` into chat, an issue or a PR.

## Related

Handbook 12 §12.2 ("How a change reaches staging"), `.github/workflows/deploy-staging.yml`, `render.yaml`,
`backend/api/migrations/env.py`, ADR-0020, ADR-0033, [`restore-from-backup.md`](restore-from-backup.md),
[`rotate-secrets.md`](rotate-secrets.md), [`README.md`](README.md).
