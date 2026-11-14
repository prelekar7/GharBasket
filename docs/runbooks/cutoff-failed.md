# Runbook: the 21:00 cut-off failed, ran late or did not run

Owner: Prathamesh Relekar. Last reviewed: 11 November 2026.

**This one is urgent.** `cutoff_lock` at T-1 21:00 IST debits each household's wallet and locks tomorrow's approved
orders (M4 §3.1). Until it runs, nothing is paid, procurement (21:10) and kitchen assignment (21:30) work on the wrong
orders, and the unpaid households have no grace period. A re-run is safe, so act first and investigate after. Aim to
have it done by 22:00, well inside the 23:00 payment grace.

## Symptoms

- **healthchecks `cutoff` is down** (Telegram and email at about 21:10): no `ok` answer from `cutoff_lock` within 10 minutes
  of 21:00.
- **healthchecks `ops`** with a body like `cutoff_lock 2026-11-10 21:00 IST failed {... "code": "platform.job_failed",
  "run_id": ...}` or `... gave_up ...`; later the 21:15 `ops_check` says `platform.failed_jobs: cutoff_lock failed: ...`.
- **Console → Cut-off** (hub manager or admin): the night's counts still show `approved` orders and `locked` 0, or the
  last run is yesterday's. The page reads `GET /v1/hubs/{id}/cutoff`; it only shows, it cannot run the cut-off.
- Customers did not get their box-locked message (or, when short, the top-up-before-the-grace message).

## Checks

1. **Did it run, and how did it end?**

   ```
   infisical run --env=staging -- just job-runs cutoff_lock
   infisical run --env=staging -- just job-runs apply_auto_approve
   ```

   - no row for tonight: the call never reached the app (go to check 2);
   - `failed` with an error: the job ran and broke (check 3);
   - `ok`, with a start time well after 21:00: it ran late, and the alert is correct but there is nothing to redo
     (Recovery step 4);
   - `ok` at 21:00: the job is fine and the alert is wrong; check that `HC_CUTOFF_URL` is set on the VM (`just jobs-vm env`
     again);
   - `skipped`: another run held the lock at that moment. Look for that run's own row.

   The `ok` summary reads `{"locked": N, "payment_failed": N, "skipped_already_locked": N, "delivery_date": "..."}`.
2. **Is the VM scheduler alive?** `just jobs-vm status 130.210.8.35 <key>` shows the service and its last calls. If it is
   down, the other evening jobs are missing too: follow [`jobs-scheduler-down.md`](jobs-scheduler-down.md) after the
   cut-off is done.
3. **Is the API or the database down?** `curl -sS -m 90 https://api.gharbasket.co.in/v1/health` (wakes a sleeping
   API in about a minute). An error mentioning the database or `asyncpg` points at Neon: check the Neon console for a
   suspended compute (the free tier's compute hours ran out) or an incident.

## Common causes and fixes

| Cause | How to tell | Fix |
|---|---|---|
| **The scheduler did not call** (VM down, service stopped) | No run row for 21:00; `scheduler` check also down; `jobs-vm status` fails or shows no 21:00 line | Run the cut-off by hand (Recovery), then [`jobs-scheduler-down.md`](jobs-scheduler-down.md) |
| **The call gave up** (Render cold start or outage longer than the scheduler's retries) | `gave_up` in the `ops` body; no run row | Wake the API (`/v1/health`), then run by hand |
| **Neon suspended or unreachable** | The run's error is a connection error; `/v1/health` answers but every job fails | Wait for Neon to recover (or for the new month if compute hours ran out), then run by hand. A suspended compute is a pilot-plan decision (Q3) |
| **A bug in a lock step** | `failed`, the same error on a re-run | The batches before the error are committed and stay locked. Fix forward: a `fix/` branch with a test, merged and deployed, then run by hand. Do not edit orders in the database |
| **Auto-approve did not run first** | `apply_auto_approve` has no 20:55 row; the cut-off locked fewer orders than expected | Run `apply_auto_approve`, then `cutoff_lock` again (both by hand, in that order) |
| **The heartbeat URL is missing** | `cutoff_lock` `ok` at 21:00, but the `cutoff` check is down | `infisical run --env=staging -- just jobs-vm env 130.210.8.35 <key>` (writes `HC_CUTOFF_URL`) |

## Recovery steps

1. **Run it by hand, with tonight's 21:00 as `scheduled_for`.** For delivery on Wednesday 11 November, the cut-off is
   Tuesday 21:00 IST = `2026-11-10T15:30:00Z`:

   ```
   gh workflow run jobs-staging.yml -f job="apply_auto_approve cutoff_lock" -f scheduled_for=2026-11-10T15:30:00Z
   ```

   Running `apply_auto_approve` with the same minute is harmless (it only approves boxes still due). The jobs work for
   the IST day after `scheduled_for`, so the same command is right at 21:20 or at 00:30. Never leave `scheduled_for`
   out after midnight.
2. **Check the result:** `just job-runs cutoff_lock` shows `ok` with tonight's `delivery_date`. On the console's Cut-off
   page `approved` is now 0 and `locked` plus `payment_needed` adds up to what was approved. A second run is fine:
   orders already locked are counted as `skipped_already_locked`, and the debit is keyed `box:<order id>`, so nobody is
   charged twice.
3. **The payment grace is fixed by `scheduled_for`, not by the run.** Unpaid households get until 23:00 IST (21:00 + 2 h)
   and a push telling them to top up before then (`orders.payment_failed`, which skips quiet hours). A late run leaves them less time; do not move
   `scheduled_for` to give more, because the later evening jobs would no longer line up.
4. **Repair what ran before the cut-off**, in this order and with each job's own minute (README, the evening chain):
   - `build_procurement_plan` (`2026-11-10T15:40:00Z`), if it ran before the cut-off finished: it builds or rebuilds
     tomorrow's plan from the locked orders. `refresh_procurement_plan` at 23:05 rebuilds it again anyway;
   - `assign_kitchens` (`2026-11-10T16:00:00Z`), if tiffins were locked after 21:30. A round only takes locked tiffins
     that no live offer holds, so it is safe; the 15-minute rounds until 00:30 continue on their own.
   - If the cut-off ran **after 23:00**, also run `payment_grace_expiry` (`…T17:30:00Z`) and then
     `refresh_procurement_plan` (`…T17:35:00Z`).
   - If it ran **after 23:15**, the routes were solved without these orders, and `solve_routes` will not solve a hub
     again once it has a plan. Re-solve each hub from the console (Routes → Solve again, then publish), as in
     [`route-solve-failed.md`](route-solve-failed.md).
5. **Tell the hub manager** by phone when the cut-off ran after 21:30: procurement and kitchen offers moved, and tomorrow's
   packing follows the new plan. Customers have already had their own messages.
6. **Bring the alerts back:** the `cutoff` check is up again at the next ok cut-off. Run `ops_check` by hand
   (`-f job=ops_check`, no `scheduled_for` needed) to clear the `ops` check now.
7. **Afterwards:** if the cause was new, add a row to the table above. If it was code, the fix needs a test.

## What not to do

- Do not lock, cancel or charge orders by hand in the database: the cut-off's debits are ledger entries keyed per order,
  and the job is the only safe way to make them.
- Do not run `payment_grace_expiry` early to "tidy up": it cancels every unpaid order of the day at once, cutting the
  grace short for households that are topping up.
- Do not run the cut-off with a `scheduled_for` from another evening: it would lock (and charge) the wrong day.

## Related

M4 §3.1 (the cut-off handshake) and §3.4, M0 §6, ADR-0021 and ADR-0033, handbook 10 §10.4, handbook 12 §12.6 ("cut-off on
time: 100% of delivery days"), [`README.md`](README.md) (running jobs by hand), `backend/api/src/gharbasket/modules/orders/jobs.py`.
