# docs/legal — customer-facing policy pages (DRAFTS)

Four pages for the public site (`gharbasket.co.in`, the console app), which Razorpay checks before live activation:

| File | Page |
|---|---|
| [terms.md](terms.md) | Terms of Service |
| [privacy.md](privacy.md) | Privacy Notice (DPDP Act 2023) |
| [refunds.md](refunds.md) | Refund, Cancellation and Delivery Policy (also the shipping/delivery policy Razorpay asks for) |
| [contact.md](contact.md) | Contact Us |

## Status: drafts, not legal advice

- Written on 2026-11-14 from the repo's specs, handbook and ADRs only. Every rule about how the service works is taken from a spec. Each page ends with an HTML comment (`<!-- Sources: … -->`) naming the file and section behind each rule, so a reviewer can check it.
- **Before anyone relies on them**, they need review by Prof. Kranti Bade and, ideally, a lawyer (and a CA for the tax lines). Handbook 13 ("Decisions & open questions") and ADR-0004 already require the legal wording to be confirmed before real money.
- The liability clause (terms §14) is deliberately left as a placeholder for the lawyer.
- When behaviour changes (a cut-off, a fee, a refund rule), update these pages in the same PR as the spec. A new privacy notice version also needs a new consent version in M1 §3.2.
- The HTML comments (the draft banner and the sources) should not render; strip them if the site's Markdown renderer shows comments.

## Placeholders

**Filled with the owner's defaults (22 Nov 2026):** referral points 250, kindness goal 500, pre-live box fee ₹30 (the config values), grievance replies acknowledged in 48 hours and resolved in 15 days, changes announced in the app (and by email if given) 7 days ahead, chats deleted 30 days after the account, daily backups kept 30 days (`backup-staging.yml`), and an interim liability clause (terms §14) that keeps every Consumer Protection Act right; the lawyer or guide may replace it. **Filled with the owner's business details (28 Nov 2026):** United Brothers Global, the registered address in Ajivali, Panvel, support email, phones and hours, Grievance Officer Yash Salunkhe, GSTIN 27DZJPR1089F1ZY, courts at Panvel, effective date 1 December 2026. There is no FSSAI number yet, so the FSSAI line is left out of terms §17 and the contact page; add it back when the licence is issued (handbook 13 §13.9, a pre-pilot gate).

**Defaults proposed on 28 Nov 2026, for the reviewer to confirm or change:**

| Where | Text now | Why |
|---|---|---|
| terms §5 | Prices include "all applicable taxes, including GST" | The business is GST-registered and the app adds nothing on top of the shown price. A CA should confirm. |
| terms §10 | Partners are paid once a week (Monday, for the week before) by bank transfer or UPI to the KYC-verified account | M8 §3.7 and M5's `weekly_payouts` (Monday 10:00 IST); during the test phase the owner pays outside the app (ADR-0037 §5) |
| refunds §3 | Pilot area: Panvel (Raigad district, Maharashtra) | The pilot plan (1–2 Panvel societies) |
| refunds §7 | Razorpay states that a normal refund usually reaches you within 5 to 7 working days | Razorpay's published time for normal refunds (M5 §3.3 uses `speed: "normal"`) |
| privacy §6 | Orders and money records kept 8 years after the end of their financial year | Covers the GST (72 months from the annual return's due date) and income-tax record periods. A CA should confirm. |

**Still open:** the R2 bucket's region and the Grafana Cloud stack's region in privacy §4 (owner: Cloudflare → R2 → bucket → Settings → Location; Grafana Cloud → the stack's details). Until both are filled, the console's test (`lib/legal-markdown.test.js`) fails and `/privacy` shows the draft banner.

## Open questions (the specs are silent, or leave it to someone else)

1. ~~**Legal identity.**~~ Decided (owner, 28 Nov 2026): United Brothers Global is the merchant and the data fiduciary; Yash Salunkhe is the Grievance Officer. Was: Who is the merchant on the Razorpay account and the data fiduciary: an individual, a firm, or United Brothers Global (handbook 00: "role and IP terms to be confirmed")? Who is the Grievance Officer?
2. **Refund time to the bank.** Proposed on 28 Nov 2026: Razorpay's 5 to 7 working days (refunds §7); confirm. M5 §3.3 sends refunds at Razorpay's `speed: "normal"` but states no timeline. The page says only "decided by Razorpay and your bank". Confirm a number (Razorpay's published normal-refund time) before stating one.
3. **Unclaimed wallet money.** M5 §3.3 / §11: money that cannot go back to a source goes to `unclaimed_balances` and "support pays it out on request", but *how* (which method, what proof) is not specified, and Prof. Bade has still to confirm the rule against RBI's closed-system PPI rules.
4. **Balance older than 180 days on a live account.** For a customer who is not deleting, the specs only allow refunds from top-ups within 180 days; the rest can only be spent. Is that acceptable, and may support pay it out as for deletion?
5. **Wallet expiry / dormancy.** No spec says whether wallet money ever expires. The pages say nothing either way.
6. **Monthly ₹20,000 top-up cap and 30-minute top-up expiry** are still marked "confirm with the guide before a real-money pilot" (M5 §11).
7. **Pre-live delivery fee.** ₹30 is a "proposed default the owner may change" (M9 §3.6). Confirm the fee for the test societies.
8. **Customer account suspension by us.** Terms §12 reserves the right to refuse claims and suspend or close accounts for misuse; no spec describes this.
9. **Minimum age 18.** Not in any spec; added as the conservative choice (DPDP treats under-18s as children needing verifiable parental consent).
10. **Revoking a required consent** (`service_delivery`, `personalisation`): M1 does not say what happens. The notice points to account deletion.
11. **Nomination right** (DPDP s.14) and **grievance response time** (DPDP Rules 2025, and the Consumer Protection (E-Commerce) Rules 2020, which as far as we know expect a grievance officer, acknowledgement within 48 hours and redress within a month): no process in the specs. The reviewer should confirm the timelines.
12. **Retention of orders and the ledger after deletion.** Proposed on 28 Nov 2026: 8 years after the financial year (privacy §6); a CA should confirm. Kept "for legal record keeping" (handbook 13 §13.6) with no period. Choose one (tax law).
13. ~~**Chat retention.**~~ Decided (owner, 22 Nov 2026): chats are kept until the account is deleted, then deleted within 30 days (privacy §6). M13 §11's 30-day proposal for chats on a live account is not adopted.
14. **Backups and erasure.** ADR-0037 §5 keeps 30 daily dumps (built in #587); no spec says whether a purged account is removed from backups or rolls off.
15. **Provider locations.** Checked on 22 Nov 2026 against each provider's own pages (sources at the end of privacy.md). Two remain: the R2 bucket's region (created with `auto`) and the Grafana Cloud stack's region. The staff console's Vercel functions run in Washington, D.C. (`iad1`, Vercel's default); moving them to Singapore (`sin1`, next to the API) would shorten every console request and take staff data out of the US. If that is changed, update privacy §4 and §5.
16. **PostHog.** ADR-0020 and handbook 12 name it, but no PostHog code exists in `frontend/` or `backend/api`, so the notice leaves it out. Add it when it ships.
17. **Tax invoices and GST.** M5 §1 lists "invoices", but only a JSON wallet statement exists (PDF later, M5 §3.2). Real sales may need tax invoices.
18. **Translations.** Handbook 13 §13.6 promises the notice in English, Hindi and Marathi; only English is drafted.
19. **Partners in the test.** KYC checks are simulated (handbook 13 §13.8, M8 §1) and payouts are simulated in the app, paid by the owner outside it (ADR-0037 §5). TDS and labour-code duties are still open (M5 §11, handbook 13 §13.8). Partner payment terms are a placeholder.
20. **"We will never ask for your OTP"** (contact page) is a safety promise, not a spec rule. Confirm the hub team's call-back script never asks for one.
21. **Anonymised research use** of real pilot data (handbook 05 `data_source = pilot`): confirm the wording with the guide.
22. **"Safe to eat"** (terms §14). The interim liability clause says we take reasonable care that orders arrive "as described, safe to eat and on the day shown". It is a duty of care, not a guarantee, but "safe to eat" may read as a warranty about food that partner kitchens and vendors prepare. Ask Prof. Bade (or the lawyer) whether to keep it, narrow it (e.g. "handled and stored as food safety rules require"), or name the FSSAI duties of each party.

## Contradictions found in the specs

1. ~~**₹1 test top-up vs the ₹100 minimum.**~~ Resolved: ADR-0037 as accepted uses a ₹100 first top-up (M5 §3.1's minimum). The pages use the ₹100 minimum.
2. **UPI details "never reach us".** M1 §3.3 says the export holds "Razorpay ids only; no card or UPI details ever reach us", but M1 §3.5 (as built) redacts `contact`, `email` and `vpa` from stored Razorpay webhook payloads, so the UPI ID *is* stored for up to 180 days. The notice says so.
3. **Purge timing.** Handbook 13 §13.5 says "deleted accounts purged within 30 days"; M1 §3.5 has a 30-day grace period, then a purge that waits for open orders, issues, refunds and carry-overs (up to 14 days) and is only flagged "overdue" 15 days after `purge_on`. The notice describes the M1 behaviour.
4. **Vercel Hobby and real payments.** Handbook 12 ("Vercel projects") says Hobby is non-commercial and "taking real payments needs the Pro plan or another host", while ADR-0037 takes live payments on the current staging, whose PWA and console are on Vercel.
5. **Notice time.** ADR-0004 and handbook 13 §13.7 say the box and pre-debit notice go out at T-3 18:00; M3 §3.1 drafts at 18:00 and sends the notice at 18:30. The pages say "evening".
6. **Carry-over limit.** ADR-0027 §6 says carried items go on the next order "not yet packed" and are refunded if none is *packed* within 14 days; M4 §3.8 attaches them only to a *locked* (paid) order and refunds rows still *pending* after 14 days. The pages follow M4 ("a paid box of yours within 14 days").
7. ~~**ADR-0037 is still "Proposed".**~~ Resolved: ADR-0037 is Accepted and on `main` (#556).
8. **Firebase OTPs.** Handbook 14 lists Firebase on "test numbers", and ADR-0020 plans the Blaze plan "for real OTPs" before the pilot. Check that real customers can get an OTP during the live test.
