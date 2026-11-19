# Runbook: switching staging to live money (the cut-over)

Owner: Prathamesh Relekar. Last reviewed: 15 November 2026.

Once, by the owner, when staging moves from Razorpay's test account to the live one ([ADR-0037](../adr/0037-live-razorpay-on-staging-with-a-cut-over.md),
M5 §3.10). The two jobs are `billing_cutover_prepare` and `billing_cutover_live`. Both refuse with
`409 billing.cutover_not_live` unless the API runs `RAZORPAY_MODE=live`, and both are safe to run again.

## Go live on staging: the whole checklist (decided 19 November 2026)

The owner chose to open GharBasket for real on the **current staging**, on the **free plans** for the team's
real-money test (Render, Vercel Hobby, Neon free). A separate production (ADR-0036, handbook 12 §12.9) is decided after
the test, before the pilot societies join. Work through these in order. Each line says who does it and how it is
checked. The cut-over itself is "Steps" below.

| # | Step | Who | Checked by |
|---|---|---|---|
| 1 | The repo PRs of the go-live plan are merged: daily backups and the dev-login guard (#587), this checklist, the console's real staff sign-in (#589), the public Terms, Refunds and Contact pages, the Budget Lock label (#586) and the console CSP | Prathamesh (merge), Yash (frontend reviews) | CI green on `main`; the staging deploy is green |
| 2 | **Console (Vercel project `ghar-basket-12to`):** `GB_API_MOCK=false` and `GB_PUBLIC_API_MOCK=false` set, **`GB_DEV_LOGIN` deleted**, the staging Firebase project's web config added as `GB_FIREBASE_API_KEY`, `GB_FIREBASE_AUTH_DOMAIN`, `GB_FIREBASE_PROJECT_ID` and `GB_FIREBASE_APP_ID` (the PWA's `VITE_FIREBASE_*` values; #589), then a redeploy | Owner | The login page shows no "Development only" block and no "Continue as" buttons; an admin's phone signs in with an SMS code |
| 3 | **Firebase (staging project):** the Blaze plan (real SMS is billed per message; there is no monthly fee); `gharbasket.co.in` and `app.gharbasket.co.in` are authorised domains; the fictional test phone numbers removed | Owner | A team member's own phone gets a real SMS code in the app and in the console |
| 4 | **PWA (Vercel project `ghar-basket`):** `VITE_FIREBASE_TEST_MODE` unset, `ORIGIN_SECRET` equal to Render's, then a redeploy | Owner | The proxy spoof checks of handbook 13 §13.3 pass (spaced out: bursts trip Cloudflare's challenge) |
| 5 | **Legal:** every placeholder in `docs/legal/` filled (business name, address, support contacts, grievance officer, FSSAI, GSTIN, dates), reviewed by Prof. Kranti Bade; then the "Draft" banners come off in a small PR; the Terms, Refunds and Contact URLs go into Razorpay's website settings | Owner, guide; Prathamesh (banner PR) | The pages at `gharbasket.co.in/terms`, `/refunds`, `/contact` and `/privacy` show no placeholder and no banner |
| 6 | **Alerts:** the healthchecks.io checks (`ops`, `scheduler`, the heartbeats) and the Grafana 5xx alert exist and are green; `/v1/ready` answers with the operator token | Owner | A test ping reaches the owner's phone; `/v1/ready` is 200 |
| 7 | **The cut-over** ("Steps" below): flush test refunds, switch the live keys and `RAZORPAY_MODE=live` together, prepare until no blockers, close, then the ₹100 test | Owner (keys), Prathamesh (jobs, each on the owner's word) | `/v1/ready` shows `payments_mode: live`; every wallet statement shows "Test balance closed"; the Razorpay dashboard matches the wallet after the ₹100 test |
| 8 | **One team-only week of real orders**, then the pilot societies. Before they join: paid plans, and staging-as-production or the separate production (ADR-0037 §6) | Team; owner decides | The week's `ops_check` runs are clean and reconciliation has no open item |

**Risks accepted for the team test (owner, 19 November 2026).**
- **The API sleeps between the job windows** (free Render). The first person to open the app after a quiet spell waits 20 to 50 seconds.
- **Vercel Hobby is for non-commercial use.** The PWA and the console take real payments through Razorpay while on it, so move them to Vercel Pro or another host before the pilot societies join.
- **`reconcile_payments` runs every 2 to 3 hours by day**, not hourly as ADR-0037 §5 says. Hourly would push Neon past its free compute budget (`infra/jobs/schedule.yaml`). The signed webhook credits each payment at once; reconciliation is the backstop for a missed one.

**Not part of this launch.** The Android app's Play release and the Razorpay-in-Capacitor test (spike S-1) are Yash's, after the PWA launch. Partner payouts stay `simulated`: the owner pays partners outside the app (ADR-0037 §5).

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
