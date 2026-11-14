# ADR-0037 — Live Razorpay payments on staging, behind a switch, with a one-time cut-over

- **Status:** Accepted (2026-11-14)
- **Date:** 2026-11-14
- **Decision ID:** D37
- **Supersedes in part:** [ADR-0036](0036-production-environment-for-the-pilot.md): its rule that live Razorpay keys exist only with `APP_ENV=prod`, and its order of work. The rest of ADR-0036 (a separate production environment, the domain plan, an own ML service) stays the target for the launch after this test.
- **Relates to:** [ADR-0004](0004-closed-loop-wallet-and-upi-autopay.md) (the wallet), M5 §3.1 (top-ups), §3.3 (refund to source), §3.4 (reconciliation), handbook 05 (money is never edited, only added), handbook 13 §13.7.

## Context
On 14 November 2026 the owner decided:
- the team tests the whole app with **real payments** on the current staging, then launches;
- free-plan limits are not a reason to wait.

The owner's Razorpay account is activated, and live keys are ready.

Today the API refuses any `RAZORPAY_KEY_ID` that does not start with `rzp_test_` (`platform/settings.py`), so no real money can move. Staging also holds money that is not real (measured on 14 November, read-only):

| What | Amount |
|---|---|
| Top-ups the demo loader posted straight to the ledger (no Razorpay payment) | 145 entries, ₹4,40,592, in `razorpay_clearing` |
| Revenue recognised from the synthetic orders | ₹4,31,992 in `sales_revenue` |
| Balances left in wallets | ₹8,600 in 16 wallets |
| Razorpay **test-mode** payments | 1 paid (₹2,000), 4 `created`, 1 `failed`; 2 `dev` payments (₹4,000) |

If live keys were simply switched on:
1. **Reconciliation** (`reconcile_payments`, M5 §3.4) and **refund to source** (M5 §3.3) would ask live Razorpay about test-mode payment ids. Razorpay would not know them, so every one would become a reconciliation item or a stuck refund.
2. **Fake balances would buy real goods.** The ₹8,600 of test money could pay for real boxes, and a refund of such a box could send real money to a test-funded wallet.
3. **The books would mix.** Revenue, clearing and wallet totals would add synthetic and real money together.
4. **Restoring staging would erase real money.** The demo loader and `staging-rollback` replace staging's database with an older copy, which would wipe ledger rows of money that really moved.

## Decision

### 1. A switch, not an environment
`RAZORPAY_MODE` is `test` (the default) or `live`.
- **`live`:**
  - `RAZORPAY_KEY_ID` must start with `rzp_live_`, and a live `RAZORPAY_WEBHOOK_SECRET` is required.
  - Live mode is allowed with `APP_ENV=staging` (this test) and `APP_ENV=prod`.
- **`test`:** a live key is refused, as today.
- **Local and test environments** refuse `live`.

The mode is set in Infisical `staging` and on Render together. It is reported by `/v1/ready` (operator token) and in the API's start-up log line, so anyone can see which money is in play.

### 2. Every payment knows its mode
A migration adds `billing.payments.mode` (`test` | `live`, NOT NULL).
- **Existing rows** are backfilled `test`, including `dev` payments.
- **New payments** take the running mode.
- **Reconciliation** lists and matches only payments of the running mode.
- **Refund to source** sends a refund part to Razorpay only for a payment of the running mode. A test-mode payment's share of a refund falls back to the wallet (M5 §3.3's existing fallback), and is then closed by step 3 if it happens before the cut-over.
- **The webhook** checks every event's signature with the running mode's secret (live mode has its own `RAZORPAY_WEBHOOK_SECRET`). An event from the test-mode webhook fails that check and is refused. Whether Razorpay's payload also names the mode is checked against Razorpay's documentation in the contract PR, not assumed here.

### 3. A one-time cut-over closes the test money
A command, `just billing-cutover-live`, run once by the owner, does the following:
1. **Confirms the host and the target mode.** It refuses unless the API is already running with `RAZORPAY_MODE=live`, and it refuses to run twice (an idempotency key).
2. **Closes the test money.** It posts one journal entry, `test_mode_closed`, that brings every account with a balance from the test period to zero:
   - wallets;
   - `razorpay_clearing`, `sales_revenue`, `unearned_revenue`;
   - any other account with a balance.

   The other side goes to a new platform account `test_funds` of a new kind `equity`. Nothing is edited or deleted (handbook 05). The entry's `details` lists each account and amount, so the closing is auditable. Kindness points are a separate currency and are left alone.
3. **Pauses synthetic households.** It sets every household created by the demo loader or simulator (tagged `data_source=synthetic`, ADR-0008) to `paused`, so no weekly plan or cut-off debit runs for them. Real team households stay active, with a ₹0 balance, and top up with real money.
4. **Writes an audit entry** with the time, the counts and the totals.

From the cut-over on:
- every real-money account starts at ₹0;
- reports and probes count only entries after `test_mode_closed`;
- the ops probe `billing.live_mode_mixed` alerts if a test-mode payment is ever credited after the cut-over.

### 4. Guards while live
While Infisical `staging` or the API says `RAZORPAY_MODE=live`, these refuse to run against staging, with a message pointing to this ADR:
- `just staging-demo` (it loads synthetic people and money);
- `just staging-rollback` (it would erase real money rows);
- the simulator's `--allow-remote`;
- `just seed` on a shared database.

The `before-demo-*` restore points stay for history, but rolling back to one is refused. A restore while live follows a new "live money" section of `restore-from-backup.md`: restore into a separate database, then rebuild with Razorpay's own record.

### 5. Backups and checks while live
- `backup-staging.yml` runs **daily** (03:43 IST), keeping 30 dumps, instead of weekly.
- `reconcile_payments` stays hourly. Its alert reaches the admins and the `ops` check, as today.
- **Payouts stay `simulated`** (M5 §3.5). How kitchens, captains and riders are actually paid during the test is the owner's manual process, outside the app.

### 6. Launch after the test
When the test ends:
- either staging becomes the production environment of ADR-0036, with fresh secrets and a fresh database (the test's live data stays in a dump);
- or the separate production environment of ADR-0036 is built.

That choice is made at the end of the test. It is not part of this ADR.

## Alternatives considered
- **Switch the keys only.** Every failure in the context list happens. Rejected.
- **A fresh database for staging now** (no cut-over). The cleanest money, but it loses the synthetic weeks the MS-A demo (13 December) and the ML evaluation use. Rejected by the owner, who chose staging with guards.
- **The separate production environment of ADR-0036 now.** The cleanest, but it is the most setup, and the free-hours limit gets in the way. Deferred to the launch (section 6).
- **Delete the test rows instead of closing them.** It is quicker, but money rows are never edited or deleted (handbook 05), and the audit trail would have a hole. Rejected.

## Consequences
- (+) Real top-ups, box debits, refunds to source and reconciliation are tested end to end against Razorpay before customers arrive.
- (+) The books stay honest. Test money is closed in one visible entry, and every live payment is marked `live`.
- (−) **Real money moves.** Every refund is a real refund, and a bug costs real rupees. The team tests with small amounts (₹100 top-ups, M5 §3.1's minimum; refunds can be as small as ₹1) from its own accounts first.
- (−) **No demo reloads while live.** A demo for the guide uses the live data that exists, or a local stack (`just dev-real`). Switching back to `test` mode is possible (set the switch, restart), but test payments then mix with live data again. It is for an emergency only.
- (−) **Staging is now holding real personal and payment data.** The DPDP duties of handbook 13 (consent, erasure, breach runbook) apply in full. The team's own phone numbers already do.
- (−) The money code change is reviewed as a money PR (handbook 11 §11.3: an Opus review and the full local check) before it merges.

## Order of work
1. This ADR (docs, the owner approves).
2. A contract PR for M5: the `mode` column, the `RAZORPAY_MODE` rules, the cut-over command, and the `billing.live_mode_mixed` probe (M5 §3.1, §3.3, §3.4).
3. The code PR (money, Opus-reviewed), then the guards PR (scripts), then daily backups.
4. Owner steps:
   - live keys and the live webhook secret in Infisical `staging` and on Render;
   - the live webhook URL in Razorpay's dashboard;
   - `RAZORPAY_MODE=live`, then `just billing-cutover-live`.
5. **The first real-money test, together.** A ₹100 top-up from the owner's phone (M5 §3.1's minimum), then a ₹1 refund of part of it and a refund of the rest, then a reconciliation run. The ledger, Razorpay's dashboard and the ops probes are checked after each step.
