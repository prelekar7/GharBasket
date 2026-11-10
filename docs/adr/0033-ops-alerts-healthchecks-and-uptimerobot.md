# ADR-0033 — Ops alerts go through healthchecks.io and UptimeRobot (free tiers)

- **Status:** Accepted (2026-11-10)
- **Date:** 2026-11-10
- **Decision ID:** D33
- **Relates to:** [ADR-0020](0020-free-tier-hosting-render-vercel-neon-r2.md) (free tiers only), [ADR-0021](0021-jobs-without-a-broker.md) (jobs as internal endpoints), [ADR-0032](0032-scheduled-jobs-from-the-oracle-vm.md) (the VM scheduler). Handbook 12 §12.5 lists the alerts; this ADR decides how they are sent.

## Context
Handbook 12 §12.5 lists six alerts for the pilot: the cut-off failed or ran late, a payment webhook backlog, a route solve failed, an error-rate spike, the API down, and Neon near its free-tier limits. None is built. The production-readiness check of 9 Nov 2026 and a code review on 10 Nov found these gaps:
- **A job that fails inside the app is seen by no one.** `scripts/jobs_scheduler.py` pings `HC_PING_URL + /fail` only when it gives up reaching the API. A job that answers 500 `platform.job_failed` is final and is only a journal line on the VM. So a failed 21:00 `cutoff_lock` would be found the next morning. `HC_PING_URL` is not set anyway.
- **A missed cut-off is not noticed either.** If the VM is down at 21:00, nothing runs and nothing complains.
- **`reconcile_payments` records an `ok` run while Razorpay is down.** `failed` is only in its summary, although M5 §3.4 says the run is `failed`.
- **Two events reach nobody:** `billing.reconciliation_mismatch` and `logistics.plan_needs_review` have no M11 route. Admins learn about a mismatch only by opening the Reconciliation page.
- **No job-run list.** There is no way to read `platform.job_runs` without database access.

Alerts must reach the team's phones within minutes, cost nothing (ADR-0020), and still work when the API or its database is down. So the thing that raises the alarm must live outside the API.

## Decision
- **healthchecks.io (free plan) is the alert hub.** It runs three checks and notifies a team Telegram group and email:

  | Check | Pinged by | Down means |
  |---|---|---|
  | `scheduler` (`HC_PING_URL`, period 1 h, grace 15 min) | The VM scheduler at every full hour (exists) | The VM or the scheduler is dead |
  | `cutoff` (`HC_CUTOFF_URL`, cron `0 21 * * *` Asia/Kolkata, grace 10 min) | The scheduler, after `cutoff_lock` answers ok | The cut-off was missed or ran late |
  | `ops` (`HC_OPS_URL`, period 12 h, grace 1 h) | `/fail`, with the error as the body: a VM job that fails or gives up, or a failed job or training workflow on Actions. A success ping: `ops_check` finding nothing wrong | A job failed, or the watchdog found a problem; the body names it |

- **UptimeRobot (free) watches `GET /v1/health` every 5 minutes**, which also keeps the free API awake, as handbook 12 already planned. It alerts by email; Telegram is not confirmed on its free plan. The ML service stays unpinged (ADR-0030).
- **A watchdog job, `ops_check`, checks what no single job sees.** Modules register probes (`@platform.ops.probe`, only in their `jobs.py`, like jobs and consumers). The job runs every probe and fails, naming each problem, when any of them finds one. It runs on the VM at 06:30, 12:30, 21:15 and 23:45 IST, inside windows that already wake Neon, so the database's sleep budget is unchanged. A schedule entry may name a `heartbeat`, the environment variable of the URL to ping when the job answers ok.
- **M11 routes the two silent events to people** as account messages, in-app and pushed:
  - `billing.reconciliation_mismatch` goes to the admins;
  - `logistics.plan_needs_review` goes to the hub's managers and the admins.
- **Error-rate alerts wait for the OpenTelemetry work** (handbook 12 §12.5, Grafana Cloud). They need logs or traces collected somewhere, which a ping service cannot do.

## Alternatives considered
- **Grafana Cloud alerting now.** It would cover error rates too. But it needs the OTel pipeline first, which is a bigger piece of work, and its alerts would depend on telemetry leaving a free Render instance that sleeps. Deferred, not rejected.
- **Alerts through our own M11 only (in-app and push).** Rejected as the only channel: it fails exactly when the API or database is down, and the console has no inbox. It is kept for the two business events above, where the API is up by definition.
- **GitHub's email on failed workflows.** It covers only the Actions runner, goes only to the person who last edited the cron, and never sees the VM's jobs. Kept as a backup, not relied on.
- **Self-hosting healthchecks on the Oracle VM.** That puts the alarm on the machine whose death it must report. Rejected.

## Consequences
- (+) A failed or missed cut-off reaches Telegram within about 10 minutes, and any job failure within a minute, with its name and error.
- (+) Everything is on free plans (checked on the official pages, 10 Nov 2026):
  - healthchecks.io Hobbyist: 20 checks with 100 log entries each; Telegram and email notifications; a `/fail` POST keeps the first 100 kB of its body.
  - UptimeRobot Free: 50 monitors at a 5-minute interval, commercial use allowed, no card.
- (−) The `ops` check goes back up at the next clean `ops_check`, even if nobody fixed the cause by hand. A failure that persists fails the next `ops_check` again, so it is reported again, at worst about 9 hours later.
- (−) Three more secrets, the ping URLs, kept in Infisical `staging`. They are copied to the VM (`just jobs-vm env`) and `HC_OPS_URL` also to GitHub Actions. A leaked ping URL can only send false alarms or hide real ones.
- Each alert names its runbook (`docs/runbooks/README.md`).

## Related
Handbook 12 §12.5 and §12.7, M0 §3 and §6 (`GET /v1/internal/jobs/runs`, probes, `ops_check`), M5 §3.4, M11 §3 (the two new message rows), `scripts/jobs_scheduler.py`, `infra/jobs/schedule.yaml`.
