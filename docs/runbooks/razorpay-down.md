# Runbook: Razorpay is down, payments do not add up, or refunds are stuck

Owner: Prathamesh Relekar. Last reviewed: 11 November 2026.

**Boxes keep going out.** The cut-off debits the GharBasket wallet, which lives in our own ledger (M5), not at Razorpay.
A household with enough balance is locked and delivered as usual. What stops is money moving in and out through
Razorpay: top-ups, and refunds back to a card or UPI. Lost webhooks are healed by `reconcile_payments`, so most of this
runbook is checking and waiting, not fixing.

## Symptoms

- **Customers cannot top up.** `POST /v1/wallet/topups` answers 503 `billing.payments_unavailable` ("Payments are not
  available right now. Try again in a few minutes.") whenever a call to Razorpay fails or times out.
- **healthchecks `ops`** from `reconcile_payments`: `reconcile_payments ... failed ... "problems": ["Razorpay is
  unavailable; nothing was compared"]`. The run changed nothing. It then shows in `ops_check` as
  `platform.failed_jobs: reconcile_payments failed: ...` until a run is ok.
- **`ops_check` lines:**
  - `billing.refunds_stuck: N refund parts stuck after 3 tries (₹…)`;
  - `billing.reconciliation_open: N reconciliation items open for > 24 h (<kind> xN); see /reconciliation`;
  - `billing.webhook_backlog: N webhooks unprocessed for > 15 min`. The webhook is processed in its own request today,
    so this line means a bug, not an outage.
- **An admin message** "Payment check: N to look at": a reconciliation run opened items.
- A customer says they paid but their wallet did not go up.

## Checks

1. **Is it Razorpay?** [status.razorpay.com](https://status.razorpay.com) and the Razorpay dashboard. An incident there
   explains the 503s and the failed runs.
2. **Is it our keys?** A 503 on every top-up while Razorpay is fine, and failed runs with an authentication error, mean
   the keys or the webhook secret on `gharbasket-api` are wrong or rotated (`RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`,
   `RAZORPAY_WEBHOOK_SECRET`; see [`rotate-secrets.md`](rotate-secrets.md)). Without keys `reconcile_payments` is
   `skipped`, which is right for dev.
3. **What did reconciliation see?** Console → Reconciliation (admin), or `GET /v1/admin/reconciliation?status=open`.
   `last_run` has its `status`, `checked`, `healed` and `opened`.
4. **Recent runs:** `infisical run --env=staging -- just job-runs reconcile_payments` and `... just job-runs
   send_refunds`. The `send_refunds` summary counts `sent`, `retrying`, `unavailable` and `stuck`.

## Common causes and fixes

| Cause | How to tell | Fix |
|---|---|---|
| **A Razorpay outage** | Their status page; 503s on top-ups; `reconcile_payments` failed | Wait. Nothing is lost: see Recovery |
| **Wrong or rotated keys** | 503s while Razorpay is up; auth errors in the API logs | Put the right keys in Infisical `staging` and on Render, then redeploy |
| **A lost payment webhook** | A customer paid; the top-up is not `paid`; Razorpay shows the payment captured | Nothing: the next `reconcile_payments` heals it (`captured_not_credited` → `healed`) and credits the wallet once. To hurry it, run the job by hand |
| **A lost refund webhook** | The refund is processed at Razorpay; the part is still `sent` here | Healed by the next `reconcile_payments` (`refund_status_mismatch`) |
| **Refund parts stuck after 3 tries** | `billing.refunds_stuck`; `send_refunds` summary `stuck` > 0 | See Stuck refunds below |

## Recovery steps

1. **During the outage,** tell the hub manager that top-ups are paused. Households already paid for tomorrow are fine.
   Households short at the cut-off cannot top up during their two-hour grace, so their boxes are cancelled at 23:00, as
   for any unpaid box. There is no switch to extend the grace; the hub can call the families it knows.
2. **When Razorpay is back,** run reconciliation and the refunds by hand instead of waiting for their next slot:

   ```
   gh workflow run jobs-staging.yml -f job="reconcile_payments send_refunds"
   ```

   No `scheduled_for` is needed: both look back over a window (72 hours for reconciliation) from now. Then check
   `just job-runs reconcile_payments`: `ok`, with `healed` covering the lost webhooks.
3. **Read the open items** on the Reconciliation page (below) and clear the `ops` alert with `-f job=ops_check`.

### Reconciliation items

Each open item is one mismatch (M5 §3.4). The job heals the two lost-webhook kinds itself; the rest need an admin:

| Kind | Means | What to do |
|---|---|---|
| `captured_not_credited` (still open) | Captured, but crediting it would pass the wallet's ₹10,000 cap, or a second payment on a paid order | That payment must go back to the customer, and neither the job nor the console can send it yet. Prathamesh refunds it in the Razorpay dashboard, the one case where that is right. The next run opens a `refund_unknown` for it; resolve both items with each other's ids in the note |
| `credited_not_captured` (critical) | We credited a wallet for a payment Razorpay does not show as captured | Check the payment in the dashboard. If it really failed, the wallet holds money it should not: an audited manual `adjustment` entry (M5 §3.4, Prathamesh only), then resolve |
| `amount_mismatch` | The amount or currency differs from the top-up's | The webhook already refused it. Check in the dashboard and resolve with what you found |
| `unknown_payment` | A captured payment for an order that is not ours | Usually another integration on the same Razorpay account. Resolve with a note, or ask Razorpay |
| `refund_unknown` (critical) | A Razorpay refund on our payment with no part here | Money left without a ledger entry: find who made it (a manual dashboard refund?) before resolving |
| `refund_stuck` | A part `pending` or `sent` for over 7 days | As below |

Resolving (`POST /v1/admin/reconciliation/{id}/resolve` with a note, from the page) records the decision only; it moves
no money.

### Stuck refunds

`send_refunds` tries each part three times (now, after 5 minutes, after 30 minutes), and never marks it failed without
Razorpay saying so. A part that ran out of tries stays `pending` and is not tried again: there is no retry button yet
(pilot backlog). Once Razorpay works again:

1. Prathamesh gives the part back one try. From the second try on, `send_refunds` first lists the payment's refunds at
   Razorpay and matches our `receipt` (the part's id): an earlier try that did reach Razorpay is found, not refunded
   twice.

   ```sql
   UPDATE billing.refund_parts SET attempts = 2, next_attempt_at = NULL
   WHERE id = '<part id>' AND status = 'pending' AND attempts >= 3;
   ```

2. Run `send_refunds` by hand and check its summary (`sent` or `found`). Write the part id and the reason in the team
   channel.

## What not to do

- Do not refund a stuck part by hand in the Razorpay dashboard: the next run would find a refund with no matching part
  (`refund_unknown`), and the part would still be sent.
- Do not credit a wallet by hand for a payment you see in the dashboard: reconciliation does it, keyed by the payment, so
  it happens once.
- Do not resolve a critical item without a note saying what you checked.

## Related

M5 §3.1 (top-ups), §3.3 (refunds to source) and §3.4 (reconciliation), M4 §3.1 (the grace), ADR-0033, handbook 13
(payments), [`cutoff-failed.md`](cutoff-failed.md), [`README.md`](README.md).
