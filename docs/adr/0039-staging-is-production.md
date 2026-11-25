# ADR-0039 — Staging is the production environment

- **Status:** Accepted (2026-11-22)
- **Date:** 2026-11-22
- **Decision ID:** D39
- **Settles:** [ADR-0037](0037-live-razorpay-on-staging-with-a-cut-over.md) §6 ("Launch after the test"). Staging becomes the production environment; no separate production is built.
- **Supersedes:** [ADR-0036](0036-production-environment-for-the-pilot.md), a separate production environment for the pilot. Its repository side (`deploy-prod.yml`, `backup-prod.yml`, `jobs-prod.yml`, the VM scheduler's `prod` target) stays built and inert.
- **Relates to:** handbook 12 §12.9, handbook 14, `docs/runbooks/live-cutover.md`.

## Context
On 19 November 2026 the owner chose to go live on the current staging, on free plans, with ADR-0037's one-time cut-over to Razorpay's live account. The separate production of ADR-0036 was to be decided after the team's real-money test.

On 22 November the owner first asked for a way to make test payments while real customers pay for real. That needs a second environment, because one deployment runs one Razorpay mode and wallets carry no mode. A plan for a separate production (live) beside staging (test) was drafted and its PRs opened (#594, #597–#600).

The owner then decided: **keep everything as it is, and make staging the final production.** No new accounts, no domain move, nothing switched over.

## Decision
1. **Staging is the production environment**, on its current services, domains (`api.`, `app.` and the bare `gharbasket.co.in`) and free plans (the risks of handbook 12 and `live-cutover.md`). No separate production is built, and the domains do not move.
2. **Real money arrives through ADR-0037's cut-over**, unchanged: `docs/runbooks/live-cutover.md`, the owner's live keys and webhook, `billing_cutover_prepare` until no blockers, `billing_cutover_live`, then the ₹100 top-up and refund test.
3. **Test payments after the cut-over** do not run on the live app. They are made:
   - on a developer's machine with Razorpay's test keys (`just dev-real` and the local stack), or
   - as small real payments refunded at once (₹100 top-up, ₹1 refund), as ADR-0037 plans.

   Switching staging back to `test` mode is an emergency step only (ADR-0037, Consequences).
4. **Firebase test numbers stay** on the live project for the engineers, on the owner's rule of 22 November: customer accounts only, never staff; each with its own non-trivial code; the list only in Infisical.
5. **The demo societies** (synthetic, from `just staging-demo`) must not be offered to real customers. That is handled before the first real customer as a follow-up: their status, or hiding synthetic societies from the onboarding list.
6. **Razorpay Checkout opens on UPI** (Google Pay, PhonePe and other apps on phones, a QR code on laptops): a frontend change, #596.

## Alternatives considered
- **A separate production with live keys, staging kept for test payments** (ADR-0036 in full; drafted on 22 November as #594, #597–#600). Gives test payments at any time and a clean database, but it needs a second set of accounts, a domain move and its own upkeep. Not chosen by the owner.
- **Tester accounts on the live system that pay with test keys.** Mixes test and real money in one ledger. Rejected.

## Consequences
- (+) Nothing new to set up or pay for. The launch path is the checklist already in `live-cutover.md`.
- (−) **No test payments on the live app after the cut-over.** New money code is tested locally with test keys, then with small real payments.
- (−) **The live database keeps the synthetic history** (closed into `test_funds` by the cut-over, synthetic households paused). Demo loads, rollbacks and the simulator stay refused while live (ADR-0037 §4).
- (−) **Free plans in production.** These limits were accepted on 19 November and stand:
  - cold starts between job windows;
  - Vercel Hobby's non-commercial terms;
  - reconciliation every 2 to 3 hours.

  Paid plans are revisited before the pilot societies join.
