# ADR-0032 — Staging's minute-level jobs are called from the Oracle VM, not cron-job.org

- **Status:** Accepted (2026-11-05, by the owner, Prathamesh)
- **Date:** 2026-11-05
- **Decision ID:** D32
- **Amends:** [ADR-0025](0025-ml-runtime-spaces-actions-r2.md), the decision "Minute-level jobs are triggered by cron-job.org". The other job trigger stays: GitHub Actions calls the daily and weekly jobs (`jobs-staging.yml`, ADR-0021). Training, R2 and the ML host are unchanged.

## Context
Since the start of November 2026, 75 cron-job.org jobs have called the 28 `cron-job.org` entries of `infra/jobs/schedule.yaml` (`POST /v1/internal/jobs/<name>` with `JOBS_TOKEN`). On 5 Nov, cron-job.org mailed that it had disabled jobs after too many failures. The jobs' histories and the API's `platform.job_runs` showed the following:
- On 4 and 5 Nov, most calls in the evening windows (18:30–23:30 IST) got **503 Service Unavailable** or **429 Too Many Requests**, in under a second, with an empty body. None of them reached the app: every 200 has a `job_runs` row, and none of the 503s or 429s does. The API itself never answers 503 to a job call.
- Calls from GitHub Actions and from a laptop in India succeeded within the same minutes. Examples are `reconcile_payments` at 16:59 UTC and a hand call to `send_refunds` at 18:04 UTC.
- The evening chain therefore failed on both evenings: `generate_weekly_plans`, `cutoff_lock`, `build_procurement_plan`, `assign_kitchens`, `payment_grace_expiry` and `solve_routes`. Twelve jobs were disabled.

Render's edge turns away calls from cron-job.org's shared addresses. Many free services are pinged from them, and Render's staff have called keep-alive pinging of free services abuse. Retrying from the same addresses does not help.

The team already runs an always-on Oracle Cloud Always Free VM in Mumbai, for the mandi collector (M10 §6). The owner opened that account in November 2026, because api.data.gov.in refuses connections from outside India. That settles the card-on-file objection of ADR-0025 and ADR-0030. The VM's address is ours alone.

## Decision
- **The VM calls the minute-level jobs.** `scripts/jobs_scheduler.py` runs as the systemd service `gharbasket-jobs` (`scripts/jobs-on-vm.sh setup|env|status|due`). At every minute it reads `infra/jobs/schedule.yaml`. For each entry with `runner: vm` whose cron names the minute in IST, it POSTs `{"scheduled_for": <the minute>}`. Each call runs in its own thread and waits up to 5 minutes, so a cold start no longer needs a keep-alive ping.
- **Retries only for answers that did not reach the app:** HTTP 429, 502, 503, 504, a connection error or a timeout. They are retried after 15 s, 45 s and 2 min with the same `scheduled_for`. The advisory lock and `scheduled_for` make a repeat harmless. The app's own answers (2xx, 401, 404, `platform.job_failed`) are final, as with any cron.
- **The schedule stays the one file.** The runner `cron-job.org` becomes `vm`, with the same crons. The service pulls `main` every 15 minutes, so a merged schedule takes effect without a redeploy. It restarts itself when its own code changes, and a state file keeps it from repeating or dropping a minute across a restart.
- **cron-job.org is retired.** Its jobs are deleted once the VM runs, and `scripts/cronjob_sync.py` and `just cronjob-sync` are removed.

## Alternatives considered
- **Keep cron-job.org and re-enable the jobs.** It costs nothing to do, but the evening chain keeps failing on most evenings. Rejected.
- **GitHub Actions schedules for everything.** Minutes are free in a public repository (ADR-0029). But GitHub starts schedules late or drops them under load, and its shortest interval is 5 minutes, so the 1-minute jobs (`send_deliveries`, `persist_gps_pings`) and the 21:00 cut-off would not fit. Rejected.
- **A paid Render instance.** It would stop sleeping, but it does not change how the edge treats cron-job.org's addresses. It also costs money the project does not have. Rejected.
- **A plain crontab on the VM.** One line per cron with curl would also work. Retries, IST crons, and keeping the schedule in one file would need a generator and a redeploy for every change. The daemon reads the file itself.

## Consequences
- (+) Calls come from one Indian address of ours, with exact minutes and retries, and the 30-second limit of cron-job.org is gone.
- (+) One service, one log: `journalctl -u gharbasket-jobs` (`scripts/jobs-on-vm.sh status`) shows every call, retry and give-up. `HC_PING_URL` (optional) pings hourly and on a give-up.
- (−) The VM is now on the critical path of staging's day. If it is down, the minute-level jobs do not run; the daily and weekly ones still run from Actions. The Oracle account must move to Pay As You Go with a budget alert, so idle Always Free VMs are not reclaimed (handbook 12).
- (−) The VM holds `JOBS_TOKEN` (in a mode-600 file, like the mandi collector's secrets). Rotating the token now also means re-running `scripts/jobs-on-vm.sh env`.
- Neon's budget is unchanged: the same crons wake the database in the same windows (`test_job_schedule.py`).

## Related
ADR-0021 (jobs as internal endpoints), ADR-0025 (amended), ADR-0029 (public repository), handbook 12 (staging operations), `infra/jobs/schedule.yaml`.
