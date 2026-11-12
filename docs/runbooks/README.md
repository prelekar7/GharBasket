# Runbooks

Owner: Prathamesh Relekar. Last reviewed: 11 November 2026.

What to do when something breaks on staging (and, from the pilot, production). Each runbook has the same parts:
Symptoms, Checks, Common causes and fixes, Recovery steps, What not to do, Related. The alerts behind them are in
[ADR-0033](../adr/0033-ops-alerts-healthchecks-and-uptimerobot.md) and handbook 12 §12.5.

## Who is on call

Prathamesh Relekar (infra, API, jobs) answers every alert. Rhythm Thakur and Manish Raje run the drills (handbook 12
§12.8) and are the second pair of eyes. The hub manager is told by phone when customers or riders are affected; the app
already tells customers what happened to their own order.

## Which runbook for which alert

Alerts arrive in the team Telegram group and by email.

| Alert | Down means | Runbook |
|---|---|---|
| healthchecks `cutoff` (cron 21:00 IST, 10 min grace) | `cutoff_lock` did not answer ok by 21:10 | [`cutoff-failed.md`](cutoff-failed.md) |
| healthchecks `scheduler` (hourly, 15 min grace) | The Oracle VM or its scheduler is not running | [`jobs-scheduler-down.md`](jobs-scheduler-down.md) |
| healthchecks `ops`, body names a job | That job failed or its call gave up | The job's runbook below, else [Any other failed job](#any-other-failed-job) |
| healthchecks `ops`, body from `ops_check` | The watchdog found a problem; each line names its probe | The probe's runbook below |
| UptimeRobot `/v1/health` | The API does not answer | [`deploy-rollback.md`](deploy-rollback.md) |
| AI health page `down`, `ml_fallback` log lines | The ML service is asleep, failing or gone | [`ml-service-down.md`](ml-service-down.md) |
| Admin message "Payment check: N to look at" | Reconciliation opened items | [`razorpay-down.md`](razorpay-down.md) § Reconciliation items |
| Hub manager message "Routes for <hub> need a look" | A route plan has problems and was not published | [`route-solve-failed.md`](route-solve-failed.md) |

### Runbooks that no alert points to

These are for a decision or a drill, not an alert:

| When | Runbook |
|---|---|
| A deploy broke staging, or code must go back | [`deploy-rollback.md`](deploy-rollback.md) |
| Staging's data is wrong or lost (restore points, the weekly dump in R2) | [`restore-from-backup.md`](restore-from-backup.md) |
| A secret leaked, someone left, or a planned rotation | [`rotate-secrets.md`](rotate-secrets.md) |
| Personal data may have been seen by someone who should not have (DPDP Act 2023) | [`dpdp-breach.md`](dpdp-breach.md) |

### Reading an `ops` alert

The body of an `ops` failure is one line. The VM scheduler writes `<job> <YYYY-mm-dd HH:MM> IST <outcome> <answer>`,
where outcome is `failed` (the app answered 500 `platform.job_failed`, with its `run_id`) or `gave_up` (the API never
answered, after four tries). A failed Actions workflow writes `<workflow>: <jobs> failed, <link to the run>` instead. For `ops_check`, the answer
carries `problems`, one per line, each `<probe>: <what>`:

| Probe | Line looks like | Runbook |
|---|---|---|
| `platform.failed_jobs` | `cutoff_lock failed: <error>`, a job with no `ok` run since | `cutoff-failed.md` for the cut-off; the job's own runbook, else [Any other failed job](#any-other-failed-job) |
| `platform.jobs_with_failed_hubs` | `<job> ended ok but hubs failed: <hub ids> (run of <time> UTC)` | [Any other failed job](#any-other-failed-job): the failed hub's error is in the API log; run the job again for the same `scheduled_for` once fixed |
| `platform.jobs_with_failed_households` | `<job> ended ok but households failed: <ids> (run of <time> UTC)` | [Any other failed job](#any-other-failed-job): the household's error is in the API log; run the job again for the same `scheduled_for` once fixed |
| `platform.outbox_stuck` | `N events unsent for > 30 min after 3 or more tries (<event> xN)` | [Any other failed job](#any-other-failed-job): a consumer keeps failing |
| `platform.database_size` | `the database is N MB, 85% of 512 MB` | Neon storage: delete old demo branches and data, or plan the paid database (Q3) |
| `billing.webhook_backlog` | `N webhooks unprocessed for > 15 min` | `razorpay-down.md` |
| `billing.reconciliation_open` | `N reconciliation items open for > 24 h (<kind> xN)` | `razorpay-down.md` § Reconciliation items |
| `billing.refunds_stuck` | `N refund parts stuck after 3 tries (₹…)` | `razorpay-down.md` § Stuck refunds |
| `logistics.plans_failed` | `box plan for <date> at hub <id> failed: <error>` | `route-solve-failed.md` |
| `logistics.plans_unreviewed` | `box plan for <date> at hub <id> is waiting for review; see /routes` | `route-solve-failed.md` |
| `orders.revenue_unrecognised` | `N delivered orders still hold unearned revenue; run recognise_delivered_revenue` | Run `recognise_delivered_revenue` by hand (idempotent, M5 §3.9), see [Run a job by hand](#run-a-job-by-hand) |
| `partners.kyc_key_missing` | `N KYC documents under key version V, which is not configured` (or `pepper version`); `KYC key version V does not decrypt`; `KYC pepper version V does not match` | `rotate-secrets.md` § KYC: put the retired key or pepper back, then run `rekey_kyc_documents` |

The `ops` check comes back up by itself at the next clean `ops_check` (06:30, 12:30, 21:15 and 23:45 IST). To bring it
back sooner after a fix, run `ops_check` by hand (below).

## Shared steps

### See what ran

```
infisical run --env=staging -- just job-runs                     # the last 20 runs of every job
infisical run --env=staging -- just job-runs cutoff_lock         # one job
infisical run --env=staging -- just job-runs "" failed 50        # the last 50 failures
```

Times are shown in IST. Each line has the run's start, the job, `ok` / `failed` / `skipped`, the minute it was for, and
its summary or error. `skipped` means another run of the same job held its lock, which is normal.

### Run a job by hand

Use the staging jobs workflow. It holds the token, so nothing secret is typed:

```
gh workflow run jobs-staging.yml -f job=cutoff_lock -f scheduled_for=2026-11-10T15:30:00Z
gh run watch "$(gh run list --workflow=jobs-staging.yml --limit 1 --json databaseId -q '.[0].databaseId')"
```

`job` may list several names separated by spaces; they run in that order and all of them run even if one fails. Then
check the result with `just job-runs <job>`.

**Always give `scheduled_for`, in UTC, for the minute the job was due.** Jobs work out their day from it, never from
the clock: an evening job (T-1) works for the IST day after `scheduled_for`. A cut-off re-run at 00:30 IST without it
would lock the orders of the day after tomorrow. Every job is safe to run twice: each holds an advisory lock, and its
writes are keyed (the cut-off's debit is `box:<order id>`).

The evening chain (T-1, IST → the UTC to type):

| Job | IST | `scheduled_for` for delivery on 11 Nov 2026 |
|---|---|---|
| `generate_weekly_plans` (T-3) | 18:00 | `2026-11-08T12:30:00Z` |
| `send_box_ready_notices` | 18:30 | `2026-11-10T13:00:00Z` |
| `remind_unapproved` | 19:00 | `2026-11-10T13:30:00Z` |
| `hold_expiry`, `schedule_redeliveries` (T, today's bags) | 20:30, 20:45 | `2026-11-10T15:00:00Z`, `…T15:15:00Z` |
| `apply_auto_approve` | 20:55 | `2026-11-10T15:25:00Z` |
| `cutoff_lock` | 21:00 | `2026-11-10T15:30:00Z` |
| `build_procurement_plan` | 21:10 | `2026-11-10T15:40:00Z` |
| `assign_kitchens` | 21:30 | `2026-11-10T16:00:00Z` |
| `payment_grace_expiry` | 23:00 | `2026-11-10T17:30:00Z` |
| `refresh_procurement_plan` | 23:05 | `2026-11-10T17:35:00Z` |
| `solve_routes` | 23:15 | `2026-11-10T17:45:00Z` |
| `build_packing_tasks` (T) | 05:00 | `2026-11-10T23:30:00Z` |
| `solve_tiffin_routes` (T) | 09:35 | `2026-11-11T04:05:00Z` |

IST is UTC + 5:30, with no daylight saving. `just jobs-vm due <ip> <key> 2026-11-10T21:00` lists the VM jobs of an IST
minute.

### Any other failed job

1. `just job-runs <job> failed` and read the error. `run_id` in the alert is the row.
2. If the error is a dependency that has recovered (the database restarting, Razorpay, the ML service), run the job by
   hand for the same `scheduled_for`.
3. If it fails the same way twice, it is a bug: open a `fix/` branch with a test that reproduces it. Most jobs repeat by
   themselves at their next cron; a daily one may need a hand run after the fix is deployed.
4. For `platform.outbox_stuck`, the API logs (Render, `gharbasket-api`) have `outbox_delivery_failed` lines with the
   event type, id, attempts and error class; the full error is in `platform.outbox.last_error`. Once fixed, the next
   `outbox_sweep` delivers the events (its retries back off up to an hour).

## What not to do

- Do not run a job without `scheduled_for` after midnight IST for the evening before.
- Do not fix data by hand in Neon's SQL editor unless a runbook says how; money rows are never updated, only added
  (handbook 05).
- Do not paste `JOBS_TOKEN` or any healthchecks URL into chat, an issue or a PR. Use `infisical run` and the workflow.

## Related

ADR-0021 (jobs), ADR-0032 (the VM scheduler), ADR-0033 (alerts), M0 §6 and §6.1, handbook 10 §10.4 (the daily
timeline), handbook 12 §12.5 and §12.7, `infra/jobs/schedule.yaml`.
