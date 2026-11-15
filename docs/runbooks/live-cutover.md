# Runbook: switching staging to live money (the cut-over)

Owner: Prathamesh Relekar. Last reviewed: 15 November 2026.

Once, by the owner, when staging moves from Razorpay's test account to the live one ([ADR-0037](../adr/0037-live-razorpay-on-staging-with-a-cut-over.md),
M5 §3.10). The two jobs are `billing_cutover_prepare` and `billing_cutover_live`. Both refuse with
`409 billing.cutover_not_live` unless the API runs `RAZORPAY_MODE=live`, and both are safe to run again.

## Before: pick the day, and tell the team

- Close on a day after the last test-paid delivery and before the next 21:00 cut-off. A delivered order blocks the
  close for 48 hours (its issue window, M4 §3.2), so in practice that is two days after the last delivery.
- Tell the team not to top up, approve boxes or ask for refunds from the switch until the close. A real top-up
  made before the close is a blocker (`live_topups_open`): the close would zero it with the test money, so it waits
  until that top-up is refunded to its source.

## Steps

1. **Flush refunds in test mode.** A test-mode refund part is never sent once the API runs live (M5 §3.10), so it
   would block the close for good. Still in `test` mode, run `send_refunds` (it also runs every 5 minutes) and then
   `reconcile_payments` by hand ([README "Run a job by hand"](README.md#run-a-job-by-hand)), until a `send_refunds` run
   sends nothing (`sent`, `found`, `retrying` all 0) and its `stuck` is 0. A stuck part is handled as in
   [`razorpay-down.md`](razorpay-down.md) "Stuck refunds".
2. **Switch the keys and the mode together.** In Infisical `staging` and on Render `gharbasket-api`, in the same
   minute: the live `RAZORPAY_KEY_ID` (`rzp_live_…`), `RAZORPAY_KEY_SECRET`, the live webhook's
   `RAZORPAY_WEBHOOK_SECRET`, and `RAZORPAY_MODE=live` ([`rotate-secrets.md`](rotate-secrets.md) "Razorpay"). Add the
   live webhook for `/v1/payments/webhook/razorpay` in the dashboard. The API refuses to start when the mode and the
   keys disagree. Check `/v1/ready` (operator token): `payments_mode` is `live`.
3. **Prepare, until there are no blockers.**
   `gh workflow run jobs-staging.yml -f job=billing_cutover_prepare`, then `just job-runs billing_cutover_prepare`. The
   first run pauses every synthetic household (no end date) and skips their weeks still open before the cut-off. Each
   run reports `blockers`:

   | Blocker | Clears when |
   |---|---|
   | `open_paid_orders` | Locked orders are delivered and 48 hours have passed, and open issues are resolved (console) |
   | `open_carry_overs` | Carried items are delivered or refunded by the hub |
   | `refund_parts_in_flight` | Parts settle (`reconcile_payments`). A test-mode part still `pending` means step 1 was missed: switch back to `test` with the test keys, flush, and switch again |
   | `unpaid_test_topups` | The unpaid test top-ups expire (minutes) |
   | `open_recon_items` | An admin resolves each item with a note (console → Reconciliation) |
   | `live_topups_open` | Each real top-up is refunded to its source in full |

   Run it again after each fix, until every count is 0.
4. **Close.** `gh workflow run jobs-staging.yml -f job=billing_cutover_live`. While a blocker remains it answers
   `409 billing.cutover_blocked` with the same `blockers`, and posts nothing. Otherwise its summary has the `entry_id`
   of the one `test_mode_closed` entry, how many `accounts` it zeroed and `total_paise`. Running it again answers the
   same entry with `"already_done": true`. Every wallet now reads ₹0, and its statement shows "Test balance closed".
   Every admin gets one inbox message and push, "Test money closed", with the amount and the number of accounts
   (M11; it waits for quiet hours like every staff message).
5. **The first real-money test, together** (ADR-0037, order of work 5): a ₹100 top-up from the owner's phone, a ₹1
   refund to source of part of it, a refund of the rest, then `reconcile_payments` by hand. After each step check the
   wallet, the Razorpay dashboard, and `ops_check`.

## Afterwards

- The probe `billing.live_mode_mixed` reports the API running live without the close for 24 hours (since the API
  started live, or since the first live payment), and any test-mode top-up credited after the close.
- An admin resumes a synthetic household only on purpose: its wallet holds no money now.
- The analytics partner earnings (console → KPIs, unit economics) count only the earnings after the close from the next
  `refresh_analytics_nightly` (02:40 IST) on; the earlier weeks' test earnings drop out at that run (M12 §3.2). The order
  money (GMV, revenue) is not cut (M12 §11).

## What not to do

- Do not switch the mode without the keys of that mode, or the keys without the mode.
- Do not run `just staging-demo`, `just staging-rollback` or the simulator against staging while live (ADR-0037 §4).
- Do not fix balances by hand: a correction is an `adjustment` entry (handbook 05).

## Related

ADR-0037, M5 §3.10, handbook 12 §12.5 and §12.7, [`rotate-secrets.md`](rotate-secrets.md),
[`razorpay-down.md`](razorpay-down.md).
