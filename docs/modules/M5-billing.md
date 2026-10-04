# M5 — Billing & Ledger

**Status:** Draft v1; §3.1 (wallet and top-ups) frozen for S2
**Owner:** Prathamesh Relekar (backend), Yash Salunkhe (screens) · **QA:** Rhythm Thakur, Manish Raje
**Phase / Sprint:** P2 / S2 (wallet); UPI Autopay in the pilot phase (P6)
**Postgres schema:** `billing` · **Python package:** `gharbasket.modules.billing`

## 1. Purpose
All money and points: the closed-loop prepaid wallet, Razorpay top-ups, UPI Autopay mandates and captures, refunds, Kindness points (separate currency with expiry), partner earnings and payouts, invoices and statements — all recorded in a **double-entry ledger**. Customer KYC is not required (closed-system wallet); payee KYC status is read from M8 before any payout.

## 2. User stories
| # | Story | Acceptance criteria |
|---|---|---|
| US-1 | As a customer, I want to top up my wallet with UPI so that weekly boxes are paid automatically. | Razorpay order → webhook → journal entry; balance updated; push sent. |
| US-2 | As a customer, I want my box paid from the wallet at cut-off so that I don't pay every week manually. | Debit posted at lock; insufficient balance → `billing.payment_failed`. |
| US-3 | As a customer, I want UPI Autopay instead of a wallet so that I'm charged per box. | Mandate created; pre-debit notice ≥ 24 h before capture; capture at cut-off. |
| US-4 | As a customer, I want refunds for shortages or failed delivery so that I only pay for what I get. | Refund to wallet instantly, or to source on request. |
| US-5 | As a customer, I want to see Kindness points and redeem them so that donations reward me. | Separate balance; redemption converts points to a discount entry; expiry respected. |
| US-6 | As a partner, I want a weekly earnings statement and payout so that income is transparent. | Statement from ledger; payout only for KYC-verified partners. |
| US-7 | As finance/admin, I want reconciliation against Razorpay so that no payment is lost. | Hourly job flags mismatches. |
| US-8 | As a customer, I want my wallet protected from misuse so that it stays safe. | Balance cap ₹10,000; no cash-out or P2P; refunds to source only. |

## 3. API endpoints
| Method | Path | Permission / role | Idempotency | Notes |
|---|---|---|---|---|
| GET | `/v1/wallet` | customer | — | Balance (paise), recent entries |
| GET | `/v1/wallet/entries` | customer | — | Older entries (cursor) |
| POST | `/v1/wallet/topups` | customer | Required | Creates Razorpay order; returns checkout payload |
| GET | `/v1/wallet/topups/{id}` | customer | — | Top-up status, polled after Checkout |
| POST | `/v1/dev/razorpay/pay` | dev and test only | — | Simulated payment for mock mode and E2E |
| POST | `/v1/wallet/refund-to-source` | customer | Required | Unused balance |
| POST | `/v1/mandates` | customer | Required | Autopay setup |
| GET / DELETE | `/v1/mandates/{id}` | customer | — | Cancel mandate |
| POST | `/v1/payments/webhook/razorpay` | Razorpay (signature) | Event id dedupe | Stores raw, processes async |
| GET | `/v1/kindness/balance` | customer | — | Points + expiry schedule |
| GET | `/v1/statements?period=` | customer, partner | — | PDF/JSON |
| GET | `/v1/admin/reconciliation` | admin | — | Mismatch report |

UPI Autopay mandates (US-3, `/v1/mandates`) are built in the pilot phase; for MS-A the box is paid from the prepaid wallet, topped up through Razorpay Checkout in test mode and debited at the 21:00 cut-off.

### 3.1 Wallet and top-up contract (frozen for S2)
The Wallet card, top-up sheet and transaction list, and the backend, build against these shapes. Changing them needs a PR reviewed by both Prathamesh and Yash. Every endpoint except the webhook needs sign-in (Bearer or the web cookie, M1 §3.1), and a customer only ever sees their own wallet.

**Money and time.** Amounts are integer paise. `amount_paise` on an entry is signed: positive into the wallet, negative out of it. Times are UTC (`2026-10-02T04:45:00Z`); the app shows them in IST. Wallet reads are never cached (`Cache-Control: no-store`).

**Closed-loop rules (ADR-0004).**
- The balance never exceeds **₹10,000** (`cap_paise`).
- Top-ups total at most **₹20,000 per calendar month** (IST).
- No cash-out, no transfers between users.
- Unused balance goes back only to the original payment source (pilot phase, `POST /v1/wallet/refund-to-source`).

```
WalletOut : {
  "balance_paise": 131000,
  "cap_paise": 1000000,
  "topup_room_paise": 869000,        what can still be added now: the cap minus the balance and
                                      unpaid top-ups, limited by the monthly allowance; 0 to 1000000
  "entries": [ Entry ]               the latest 20, newest first; older ones via /v1/wallet/entries
}
Entry : {
  "id": "<uuid>",                    the journal entry
  "kind": "topup" | "topup_bonus" | "box_debit" | "refund" | "adjustment",
                                      show a generic label for a kind the app doesn't know yet
  "amount_paise": -69000,
  "ref": { "type": "topup", "id": "<uuid>" }
       | { "type": "order", "id": "<uuid>", "delivery_date": "2026-10-06" }
       | null,
  "at": "2026-10-05T15:30:00Z"
}

GET /v1/wallet                        200 WalletOut     (a user without a wallet yet gets 0 and [])
GET /v1/wallet/entries?cursor=&limit= 200 Page[Entry]   newest first; limit ≤ 100, default 50

POST /v1/wallet/topups                Idempotency-Key required (one key per amount the user picks)
  request : { "amount_paise": 200000 }          ₹100 to ₹10,000, whole rupees (a multiple of 100)
  201 : TopUpOut                                 the same key replays the same answer
  TopUpOut : {
    "id": "<uuid>",
    "razorpay_order_id": "order_EKwxwAgItmmXdp",
    "key_id": "rzp_test_…",                      pass to Razorpay Checkout as `key`
    "amount_paise": 200000,
    "currency": "INR",
    "status": "created" | "paid" | "failed" | "expired" | "refunded",
    "created_at": "…"
  }
GET /v1/wallet/topups/{id}            200 TopUpOut | 404 "billing.topup_not_found"
                                      after Checkout succeeds, poll this (every 2 s, up to 30 s)
                                      until "paid", then refetch /v1/wallet: the webhook credits
                                      the wallet, never the app

POST /v1/payments/webhook/razorpay    Razorpay only; no sign-in. Not for the app.
  The raw body is checked against X-Razorpay-Signature (HMAC-SHA256 with the webhook secret);
  a bad signature gets 401. Each event is stored once by its x-razorpay-event-id, then processed:
  payment.captured for a top-up's order → status "paid" and one journal entry
  (razorpay_clearing → wallet). The same event or payment is never credited twice. payment.failed
  → "failed". Unknown events are stored and ignored. 200 once stored and processed; any other
  answer makes Razorpay deliver it again.

POST /v1/dev/razorpay/pay             only with APP_ENV=dev or test (404 otherwise)
  request : { "razorpay_order_id": "order_…" }
  200 : { "razorpay_payment_id": "pay_dev_…", "status": "captured" }
  Stands in for Checkout plus the webhook: it runs the same processing as a real
  payment.captured event, so mock mode, E2E tests and local runs need no Razorpay account.
```

- **Unpaid top-ups:** a top-up stays `created` for 30 minutes, holding its amount against the cap, then becomes `expired`.
- **Late payments:** a payment that still arrives for an expired top-up is credited if it fits under the cap. If it doesn't fit, it is refunded to its source and the top-up ends as `refunded`.

**Error codes**

| Status | Code | When |
|---|---|---|
| 422 | `billing.topup_too_small` | Under ₹100, or not whole rupees |
| 422 | `billing.wallet_limit` | Balance plus unpaid top-ups plus this one would pass ₹10,000; the problem carries `"room_paise"` |
| 422 | `billing.monthly_topup_limit` | This month's top-ups would pass ₹20,000; the problem carries `"room_paise"` |
| 429 | `billing.too_many_topups` | More than 10 top-ups started in an hour |
| 503 | `billing.payments_unavailable` | Razorpay is not configured or not answering; try again later |
| 404 | `billing.topup_not_found` | Unknown top-up, or someone else's |
| 400 / 422 | `request.idempotency_key_required`, `request.idempotency_mismatch`, `request.invalid` | As in M0 |

**For the mobile mocks** (current shapes → contract)

| Mock today | Contract |
|---|---|
| `entries[].id` like `led-1` | A UUID |
| `ref: "2026-10-06"` on a box debit | `ref: { type: "order", id, delivery_date }`; show `ref.delivery_date` |
| Times with `+05:30` | UTC with `Z`; format in IST on screen |
| No cap in `GET /v1/wallet` | `cap_paise` and `topup_room_paise`: use the room to grey out amounts that won't fit |
| Wallet credited when Checkout's handler resolves | Poll `GET /v1/wallet/topups/{id}` until `paid`, then refetch the wallet |
| `/v1/dev/razorpay/pay` (mock only) | Same path and body, served by the real API in dev and test |
| `/v1/mandates`, `/v1/kindness/balance` | Not in S2: Autopay comes in the pilot phase, Kindness points with M9 donations. Keep the mocks. |

**For other modules** (`billing/api.py`, no HTTP). M4's cut-off and refunds call these inside their own transaction:
- `balance(session, user_id) -> int`
- `debit_for_order(session, user_id, order_id, amount_paise, delivery_date)` → one `box_debit` entry (wallet → unearned_revenue), keyed by the order id, so a retried cut-off never debits twice. It raises `InsufficientBalance` (code `billing.insufficient_balance`), which M4 turns into `PAYMENT_FAILED`.
- `refund_to_wallet(session, user_id, order_id, amount_paise, reason)` → one `refund` entry (unearned_revenue → wallet), keyed by order id and reason.

## 4. Tables owned
| Table | Key columns |
|---|---|
| `billing.ledger_accounts` | id, owner_type, owner_id, currency (INR, KINDNESS_PTS), kind (asset, liability, revenue, expense) |
| `billing.journal_entries` | id, kind, ref_type, ref_id, details jsonb (e.g. delivery_date, reason), idempotency_key (unique), created_at; append-only |
| `billing.postings` | entry_id, account_id, amount (signed minor units: debit +, credit −) — deferred trigger enforces Σ = 0 per currency; append-only |
| `billing.payments` | id, user_id, provider, provider_ref, amount_paise, status, purpose |
| `billing.mandates` | id, user_id, provider_ref, max_paise, status, last_notice_at |
| `billing.payment_webhooks` | id, provider, event_id (unique), payload jsonb, processed_at |
| `billing.payouts` | id, partner_id, week, amount_paise, status, provider_ref |
| `billing.points_lots` | id, user_id, points, expires_at, source_ref |

### 4.1 Ledger rules (handbook 05 §5.7)
- **Accounts:** one account per (owner, currency, purpose), created on first use: `wallet:<user>` (liability, INR) for customers; platform accounts `razorpay_clearing`, `unearned_revenue`, `sales_revenue` and `promo_expense` are seeded by the migration.
- **Entries:** a journal entry has two or more postings; a deferred constraint trigger rejects the transaction unless they sum to zero per currency. Entries are never updated or deleted; a mistake is corrected by a new `adjustment` entry.
- **Exactly once:** every entry has a unique `idempotency_key` built from what caused it (`topup:<payment id>`, `box:<order id>`, `refund:<order id>:<reason>`). Posting the same key again returns the existing entry.
- **Balances** are sums of postings, never a stored column. A posting to a wallet first locks that wallet's account row, so two debits at the same moment cannot both pass the balance check. The wallet never goes below zero or above the cap.
- **Tests:** Hypothesis property tests for these four invariants (§9), plus a test that a duplicate webhook delivery credits once.

## 5. Events
**Published:** `billing.wallet_topped_up`, `billing.payment_captured`, `billing.payment_failed`, `billing.refund_issued`, `billing.points_credited`, `billing.payout_sent`.
**Consumed:** `orders.locked` (debit/capture request via api), `orders.delivered` (recognise revenue, captain commission), `orders.delivery_failed`, `orders.shortage_substituted`, `community.donation_verified` (points), `partners.assignment_completed` (earnings).

## 6. Jobs
| Job | Schedule | Notes |
|---|---|---|
| `process_webhooks` | Outbox consumer after the webhook is stored | Idempotent by event_id |
| `send_pre_debit_notices` | T-3 18:30 | Autopay only |
| `reconcile_payments` | Hourly | Razorpay API vs ledger |
| `expire_kindness_points` | Daily 00:30 | FIFO by lot |
| `weekly_payouts` | Monday 10:00 | Verified partners only (test mode) |

Scheduled jobs run as internal job endpoints (`POST /v1/internal/jobs/<name>`) called by a GitHub Actions schedule (ADR-0021). Razorpay calls go through our own thin async client on httpx2, not the official SDK.

## 7. Permissions
| Permission | Roles (scope) |
|---|---|
| `wallet:read/write` | customer (own) |
| `statements:read` | customer, partner (own); franchise_owner (hub aggregates) |
| `reconciliation:read` | admin |

## 8. Screens
**Mobile:** Wallet card + top-up, transaction list, Autopay setup, Kindness points (with M9 gullak), partner earnings and statements. **Console:** reconciliation, payouts, refunds review.

## 9. Tests required
- Property (hypothesis): every journal entry sums to zero per currency; wallet never negative; replaying an idempotency key never double-posts; points never redeemed after expiry.
- Integration: webhook signature verification; duplicate webhook delivery; reconciliation mismatch detection.
- E2E: top-up in Razorpay test mode inside the Capacitor app (Phase 1 spike).

## 10. Dependencies
M0, M1 (users), M4 (orders via events/api), M8 (payee KYC status via `partners.api`), M11 (notices).

## 11. Open questions
- Monthly top-up limit (proposed ₹20,000 in §3.1) and the 30-minute top-up expiry: confirm with the guide before a real-money pilot.
- Top-up bonus rules (e.g. ₹2,000 → ₹2,100) and their accounting as promo expense.
- Captain commission per order value for the pilot (proposed ₹5–8).
