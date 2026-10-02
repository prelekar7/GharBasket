# M5 — Billing & Ledger

**Status:** Draft v1 — refine at phase start (spec-first)
**Owner:** Yash Salunkhe · **Reviewer:** Prathamesh Relekar · **QA:** Rhythm Thakur, Manish Raje
**Phase / Sprint:** P2 / S2 (Autopay in S3, stretch-safe)
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
| POST | `/v1/wallet/topups` | customer | Required | Creates Razorpay order; returns checkout payload |
| POST | `/v1/wallet/refund-to-source` | customer | Required | Unused balance |
| POST | `/v1/mandates` | customer | Required | Autopay setup |
| GET / DELETE | `/v1/mandates/{id}` | customer | — | Cancel mandate |
| POST | `/v1/payments/webhook/razorpay` | Razorpay (signature) | Event id dedupe | Stores raw, processes async |
| GET | `/v1/kindness/balance` | customer | — | Points + expiry schedule |
| GET | `/v1/statements?period=` | customer, partner | — | PDF/JSON |
| GET | `/v1/admin/reconciliation` | admin | — | Mismatch report |

## 4. Tables owned
| Table | Key columns |
|---|---|
| `billing.ledger_accounts` | id, owner_type, owner_id, currency (INR, KINDNESS_PTS), kind (asset, liability, revenue, expense) |
| `billing.journal_entries` | id, kind, ref_type, ref_id, idempotency_key (unique), created_at |
| `billing.postings` | entry_id, account_id, amount (signed minor units) — deferred trigger enforces Σ = 0 per currency |
| `billing.payments` | id, user_id, provider, provider_ref, amount_paise, status, purpose |
| `billing.mandates` | id, user_id, provider_ref, max_paise, status, last_notice_at |
| `billing.payment_webhooks` | id, provider, event_id (unique), payload jsonb, processed_at |
| `billing.payouts` | id, partner_id, week, amount_paise, status, provider_ref |
| `billing.points_lots` | id, user_id, points, expires_at, source_ref |

## 5. Events
**Published:** `billing.wallet_topped_up`, `billing.payment_captured`, `billing.payment_failed`, `billing.refund_issued`, `billing.points_credited`, `billing.payout_sent`.
**Consumed:** `orders.locked` (debit/capture request via api), `orders.delivered` (recognise revenue, captain commission), `orders.delivery_failed`, `orders.shortage_substituted`, `community.donation_verified` (points), `partners.assignment_completed` (earnings).

## 6. Jobs
| Job | Schedule | Notes |
|---|---|---|
| `process_webhooks` | Continuous | Idempotent by event_id |
| `send_pre_debit_notices` | T-3 18:30 | Autopay only |
| `reconcile_payments` | Hourly | Razorpay API vs ledger |
| `expire_kindness_points` | Daily 00:30 | FIFO by lot |
| `weekly_payouts` | Monday 10:00 | Verified partners only (test mode) |

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
- Top-up bonus rules (e.g. ₹2,000 → ₹2,100) and their accounting as promo expense.
- Captain commission per order value for the pilot (proposed ₹5–8).
