# Runbook: the job scheduler on the Oracle VM is down

Owner: Prathamesh Relekar. Last reviewed: 11 November 2026.

Staging's minute-level and evening jobs (the `vm` entries of `infra/jobs/schedule.yaml`) are called by
`scripts/jobs_scheduler.py`, the systemd service `gharbasket-jobs` on the Oracle Always Free VM in Mumbai (`130.210.8.35`,
ADR-0032). The same VM runs the mandi collector. While the scheduler is down **no `vm` job runs**: no cut-off, no
procurement, no routes, no refunds, no outbox sweep. The daily `actions` jobs on GitHub keep running. **Missed minutes are
not replayed**: after a restart the scheduler starts from the current minute. The jobs that mattered in the gap have to
be run by hand.

## Symptoms

- **healthchecks `scheduler` is down.** The scheduler pings it every full hour, with 15 minutes of grace, so the alert
  comes 15 to 75 minutes after it stopped.
- **healthchecks `cutoff`** is down at 21:10, if the gap covers 21:00 ([`cutoff-failed.md`](cutoff-failed.md) first).
- **`ops` stays quiet at first:** nobody is calling the VM jobs, so none of them fails. `ops_check` is a VM job too, so
  the `ops` check goes down only when its 12 hours (plus 1 hour of grace) pass without a clean run. A missing run is not
  a failed one.
- `just job-runs` has no rows for `outbox_sweep` or the evening jobs since a certain time.

## Checks

1. **Can you reach it?**

   ```
   just jobs-vm status 130.210.8.35 <ssh key>
   ```

   It prints the service's state and its last 25 journal lines (`started`, each call, `schedule_unreadable`,
   `restarting for new code`). An SSH timeout means the VM itself is down or unreachable (check 3).
2. **What would it have called?** `just jobs-vm due 130.210.8.35 <ssh key> 2026-11-10T21:00` lists the jobs of an IST
   minute, from the schedule the VM has.
3. **Oracle console** (cloud.oracle.com, region Mumbai): is the instance running? Oracle stops an Always Free instance it
   counts as idle (low CPU, network and memory over 7 days) and emails the account first; a stopped instance can be
   started again from the console. Oracle says it does not charge for Always Free resources on a Pay As You Go account,
   and that upgrading avoids idle reclamation.
4. **Is it the API instead?** If the journal shows calls ending `gave_up` (429, 502–504, timeouts), the scheduler is
   fine and Render is the problem: wake it (`curl -sS -m 90 https://gharbasket-api.onrender.com/v1/health`) and see
   [`deploy-rollback.md`](deploy-rollback.md).

## Common causes and fixes

| Cause | How to tell | Fix |
|---|---|---|
| **The service crashed or stopped** | `status` shows `inactive` or `failed` | `ssh -i <key> ubuntu@130.210.8.35 sudo systemctl restart gharbasket-jobs`, then `status` again. systemd restarts it by itself 5 s after a crash, so a stopped service usually means someone stopped it or its file is broken |
| **Missing or wrong secrets** | `status` shows it exiting at start with "API_URL and JOBS_TOKEN are needed", or calls answer 401 | `infisical run --env=staging -- just jobs-vm env 130.210.8.35 <key>`: rewrites `~/.config/gharbasket/jobs.env` and restarts the service. Needed after every new `JOBS_TOKEN` |
| **A broken pull** | `schedule_unreadable` in the journal, or the checkout has local changes | The scheduler keeps its last good schedule. Fix `schedule.yaml` on main (`test_job_schedule.py` catches most mistakes), or reset the VM's checkout: `ssh … 'git -C GharBasket fetch -q && git -C GharBasket reset --hard origin/main'` |
| **The VM is stopped** | SSH times out; the Oracle console shows it stopped | Start it in the Oracle console. The service starts at boot |
| **The VM is gone** (reclaimed, terminated) | No instance in the console | Create a new Always Free instance (Ubuntu, Mumbai), add the SSH key, then `just jobs-vm setup <ip> <key>` and `infisical run --env=staging -- just jobs-vm env <ip> <key>`. Update the address here, in handbook 12 and in ADR-0032. The mandi collector needs its own setup again |
| **The clock is wrong** | Calls in the journal at the wrong minutes | `timedatectl` on the VM; the scheduler reads its crons in `Asia/Kolkata` but needs the system clock right (NTP) |

## Recovery steps

1. **If it is near 21:00 and the VM will not be back in minutes,** do not wait: run the evening chain from GitHub
   instead ([`README.md`](README.md), the evening chain), the cut-off first:

   ```
   gh workflow run jobs-staging.yml -f job="apply_auto_approve cutoff_lock" -f scheduled_for=2026-11-10T15:30:00Z
   ```

   then each later job of the evening with its own minute, as its time comes.
2. **Bring the scheduler back** with the fix from the table, and check `just jobs-vm status` shows `started` and calls
   at the next minutes.
3. **Run what was missed.** From the time it stopped to the time it came back:
   - the evening chain and the morning jobs (`build_packing_tasks` at 05:00, `solve_tiffin_routes` at 09:35,
     `close_routes` at 16:00): run each by hand with its own `scheduled_for`;
   - the every-few-minutes jobs (`outbox_sweep`, `send_refunds`, `build_data_export`, `persist_gps_pings`, the tiffin
     offer rounds) catch up at their next run; nothing to do;
   - check with `just job-runs` that each missed job now has an `ok` row for its day.
4. **Tell the hub manager** if a job that touches the hub ran late (cut-off, procurement, routes, packing tasks).
5. The `scheduler` check comes back at the next full-hour ping. Run `ops_check` by hand to clear the `ops` check.

## What not to do

- Do not start a second scheduler (another machine, a laptop, cron-job.org) while the VM one might come back: each would
  call every job. A job's lock makes two calls safe, but the alerts and the journal would no longer tell the truth.
- Do not run the missed minute-level jobs for every minute of the gap: their next run does all the work.
- Do not copy `jobs.env` off the VM or print it; it holds `JOBS_TOKEN` and the healthchecks URLs.

## Related

ADR-0032 (the VM scheduler) and ADR-0033 (alerts), handbook 12 §12.1 (what runs where) and §12.7,
`scripts/jobs_scheduler.py`, `scripts/jobs-on-vm.sh`, `infra/jobs/schedule.yaml`, [`README.md`](README.md),
[Oracle: Always Free resources](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm).
