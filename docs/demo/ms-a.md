# MS-A demo: Commerce core (13 December 2026)

**What MS-A must show** (handbook 14 §14.2): a customer onboards, receives a rule-based weekly box, edits it, pays from the wallet; the 21:00 cut-off locks orders and posts ledger entries.

The demo runs **live on staging**: the real PWA at `https://app.gharbasket.co.in`, the real API at `https://api.gharbasket.co.in`, Razorpay in test mode. The evening jobs of the week (box drafting at T-3 18:00, the box-ready notice at 18:30, the cut-off at T-1 21:00) are run ahead of the clock by the operator, through the same job endpoints the scheduler calls, with `scheduled_for` set to their real times. So the whole week fits in about ten minutes. If anything on staging fails on the day, switch to the recorded run (below).

## Roles

| Who | Screen | Does |
|---|---|---|
| **Presenter** | A phone (Chrome, Android) or a laptop browser in phone size, mirrored to the projector | Everything the customer does, in the real PWA |
| **Operator** (Prathamesh) | A laptop terminal in the repo, Infisical signed in | The jobs (`just demo-ms-a …`) and the ledger report |
| Narrator (optional) | — | Says what happens behind the screen at each step |

## The script

The times are a guide (about 10 minutes). **T** is the demo society's next delivery day. `just demo-ms-a day` prints it with every job's time. For a demo on Sunday 13 December, T is Tuesday 15 December and the cut-off is Monday 14 December, 21:00.

Run every operator command as `infisical run --env=staging -- just demo-ms-a <step>`. Below it is shortened to `demo <step>`.

| # | Time | Presenter (PWA) | Operator | What to point at |
|---|---|---|---|---|
| 1 | 0:00 | Open `app.gharbasket.co.in`, choose the language, sign in with the **demo phone number** and its Firebase test code | — | Phone OTP sign-in (Firebase), no password; three languages |
| 2 | 0:45 | Onboarding: society **Sunrise Towers**, flat, household (size, ages, diet), Autopilot on | — | Society-based delivery; the household profile drives the plan |
| 3 | 2:00 | Onboarding's wallet step: choose **Other** and top up **₹5,000**. The default ₹2,000 is often short: staging's boxes average about ₹3,000 with add-ons, and a short wallet turns step 7 into the grace path. Pay in Razorpay Checkout with a test card or test UPI (Razorpay's test-mode details) | If the balance has not changed 30 s after paying: `demo reconcile` | Wallet first: no card on file; the webhook credits the wallet, never the app |
| 4 | 3:00 | — | `demo plan` (T-3 18:00) and then `demo announce` (T-3 18:30) | "Every evening at 18:00 the planner drafts each family's box three days ahead." The summary shows `created: 1` |
| 5 | 3:30 | Reload **This week**: the box is there, with its total and the cut-off time | — | The box is planned from the profile and the hub's catalogue (CP-SAT, or the rule-based fallback if the ML service is slow; both are valid for MS-A) |
| 6 | 4:30 | Edit: remove one item, change a quantity, add an add-on. Show the total change. **Approve** | — | Edits are free until the cut-off; prices come from the price list, never from the AI |
| 7 | 6:00 | — | `demo cutoff` (T-1 21:00). The summary shows `locked: 1` | "At 21:00 the cut-off locks every approved box and pays it from the wallet, in one transaction." |
| 8 | 6:30 | Refresh: the order is **locked and paid**; the wallet shows the debit next to the top-up | — | No edits after the cut-off: the hub can now buy exactly what was ordered (zero waste) |
| 9 | 7:30 | — | `demo ledger <demo phone>` | Double entry: the top-up is `razorpay_clearing → wallet`, the box is `wallet → unearned_revenue`; each entry sums to zero and so does the whole ledger. Money is integer paise |
| 10 | 9:00 | Questions | — | — |

Optional if there is time: **the short-wallet path.** Before step 7, a second phone with a small balance approves a box. The cut-off then marks that order `payment_failed` with a 2-hour grace. A top-up within the grace pays it; `demo grace` (T-1 23:00) cancels it if still unpaid. Leave it out of the main run: it doubles the phone work.

The console at `gharbasket.co.in` serves demo data until its staff sign-in is built (handbook 12 §12.2), so it is not part of the live run. Show it only as the UI of the hub's day, and say that it is demo data.

## Before the day

**One week before (rehearsal 1, about 6 December), then again the day before (rehearsal 2):**

1. **Sites up.** `app.gharbasket.co.in` opens the PWA and `gharbasket.co.in` the console, each with a padlock. `curl https://app.gharbasket.co.in/v1/health` answers `"status":"ok"` through the Vercel rewrite.
2. **Firebase.** `app.gharbasket.co.in` is under Authentication → Settings → Authorized domains. Phone numbers for testing has one **unused number per run**: rehearsal 1, rehearsal 2, the demo and a spare. A number that has already onboarded skips onboarding, so it cannot be the demo number. `VITE_FIREBASE_TEST_MODE=true` is set on the PWA's Vercel project.
3. **Razorpay test mode.**
   - Render has `RAZORPAY_KEY_ID` (`rzp_test_…`), `RAZORPAY_KEY_SECRET` and `RAZORPAY_WEBHOOK_SECRET`. Without them top-ups answer 503.
   - The Razorpay test dashboard has a webhook to `https://api.gharbasket.co.in/v1/payments/webhook/razorpay` for `payment.captured` and `payment.failed`, with the same secret. A missing webhook is healed by `demo reconcile`, but it is slower.
4. **Operator laptop.** `infisical run --env=staging -- just demo-ms-a check` prints the API's health, "Jobs token: accepted" and T. `just demo-ms-a ledger +919800000000` (any number) answers "No user with phone …": the database URL works.
5. **The demo society** is `live` with a delivery day. `just demo-ms-a day` fails with a message if not. Use `DEMO_SOCIETY=<slug>` for another one.
6. **A full run** of the script with the rehearsal number, timed. Note anything slow.
7. **The recorded run is fresh** (below), from the commit staging runs.

**On the day, 30 minutes before:**

- `infisical run --env=staging -- just demo-ms-a check`. This also wakes the API (Render's free tier sleeps; the first call can take a minute).
- Open `app.gharbasket.co.in` on the presenter's phone and stay on the sign-in screen.
- No merges to `main` from an hour before until the demo ends: a deploy restarts the API mid-demo.
- Run `ops_check` (`gh workflow run jobs-staging.yml -f job=ops_check`) and confirm it is clean, so a problem shown later is not an old one.

## If something fails

| Symptom | Do |
|---|---|
| Sign-in refused (`auth/unauthorized-domain`) | Firebase authorized domains (checklist 2). If it cannot be fixed in a minute, use the recorded run |
| Top-up does not arrive in the wallet | `demo reconcile`, then refresh. Razorpay test mode can take a few seconds |
| `demo plan` shows `created: 0` | The household is not in the demo society or Autopilot is off: check onboarding. `skipped` means no subscription, paused, or no delivery day on T's weekday |
| The box is not shown after `demo plan` | Run `demo announce`: the box is shown to the household only after the notice job |
| `demo cutoff` shows `payment_failed: 1` | The wallet was short. Top up within the grace (2 h of the scheduled cut-off), then show the paid order |
| The API is slow or down | `just demo-ms-a check`. If Render is down, switch to the recorded run |

## The recorded run (backup)

The same journey runs in a real browser on the real backend locally: `e2e/web/journeys/ms-a-golden-week.spec.js` (e2e/README.md). A fresh customer onboards in the real screens, T-3 18:00 plans, 18:30 announces, the customer edits and approves, the T-1 21:00 cut-off locks and pays, and the hub manager sees it in the console (the console does run on the real API locally).

Record it the week before and again after any app change:

```
just up
just e2e-web journeys/ms-a-golden-week.spec.js --headed --trace on   # and record the screen (macOS: Cmd+Shift+5)
pnpm --filter @gharbasket/e2e exec playwright show-trace <trace.zip from e2e/web/test-results>
```

## After the demo

The demo order stays on staging. Staging's own jobs on T handle it like any other order, starting with packing at T 05:00. Nothing needs to be cleaned up. The next `ops_check` reports anything stuck.
