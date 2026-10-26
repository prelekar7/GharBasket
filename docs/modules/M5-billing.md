# M5 — Billing & Ledger

**Status:** Draft v1; §3.1 (wallet and top-ups) frozen for S2; §3.2 (customer statements) frozen on 2026-10-08; §3.3–§3.6 (refund to source, reconciliation, partner payouts, Kindness points) drafted 2026-10-09, frozen on approval; §3.7 mandates deferred (cut-able); §3.8 (order refunds list, served by M4) drafted 2026-10-25, frozen on approval
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
| US-5 | As a customer, I want to see Kindness points and redeem them so that donations reward me. | Separate balance; points are redeemed only as a donated meal ("a meal for a child") through M9's partners, never as cash or money off a box; expiry respected. |
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
| POST | `/v1/wallet/refund-to-source` | customer | Required | Unused balance back to the paying UPI/card (§3.3) |
| GET | `/v1/wallet/refunds[/{id}]` | customer | — | Refund status (§3.3) |
| POST | `/v1/mandates` | customer | Required | Autopay setup; **not in this phase** (§3.7) |
| GET / DELETE | `/v1/mandates/{id}` | customer | — | Cancel mandate; **not in this phase** (§3.7) |
| POST | `/v1/payments/webhook/razorpay` | Razorpay (signature) | Event id dedupe | Stores raw, processes async |
| GET | `/v1/kindness/balance`, `/v1/kindness/entries` | customer | — | Points, expiry schedule and history (§3.6) |
| GET | `/v1/payouts` | partner (own) | — | Weekly payouts (§3.5) |
| GET / POST | `/v1/admin/payouts`, `/v1/admin/payouts/{id}/retry` | admin, hub_manager (hub, read) | Required (POST) | §3.5 |
| GET | `/v1/statements?period=YYYY-MM` | customer | — | The wallet's month from the ledger, JSON (§3.2); PDF later; a partner's earnings with `account=earnings` (§3.5) |
| GET | `/v1/admin/reconciliation` | admin | — | Mismatch report (§3.4) |
| POST | `/v1/admin/reconciliation/{id}/resolve` | admin | — | Close an item with a note; moves no money (§3.4) |
| GET | `/v1/admin/refunds?hub_id=&from=&to=` | hub_manager (hub), admin | — | Read-only list of order refunds; served by M4's router from M5's facade (§3.8) |

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
  → "failed". Unknown events are stored and ignored. 200 { "outcome": "credited" | "duplicate" |
  "already_settled" | "failed" | "refunded_over_cap" | "unknown_order" | "amount_mismatch" |
  "malformed" | "ignored" } once stored and processed; any other answer makes Razorpay deliver
  it again. Without RAZORPAY_WEBHOOK_SECRET configured: 503 "billing.payments_unavailable".

POST /v1/dev/razorpay/pay             only with APP_ENV=dev or test (404 otherwise)
  request : { "razorpay_order_id": "order_…" }
  200 : { "razorpay_payment_id": "pay_dev_…", "status": "captured" }
  Stands in for Checkout plus the webhook: it runs the same processing as a real
  payment.captured event, so mock mode, E2E tests and local runs need no Razorpay account.
  Only the top-up's owner may pay it. Not in openapi.json (call it with a plain request).
  Without Razorpay keys, APP_ENV dev and test create dev orders (`order_dev_…`, key_id
  `rzp_test_dev`); a deployed environment answers 503 instead.
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
- `refund_to_wallet(session, user_id, order_id, amount_paise, reason)` → one `refund` entry (unearned_revenue → wallet), keyed by order id and reason. Order issues pass `reason="issue_<issue id hex>"` (M4 §3.2), so each issue is refunded at most once. A refund the wallet cannot take goes to the payment source in the same call (§3.3, "Order refunds when the wallet is full"); `billing.wallet_limit` is left only for a refund the top-ups cannot take either.
- `export_for_user(session, user_id) -> dict` → the user's wallet entries and top-ups for the data export (M1 §3.3).

### 3.2 Customer statement contract (frozen on 2026-10-08)
The wallet statement screen and the backend build against this. Same sign-in, money, time and review rules as §3.1; a customer only ever sees their own wallet.

```
GET /v1/statements?period=2026-10        Cache-Control: no-store
  200 : {
    "period": "2026-10", "currency": "INR",
    "opening_paise": 50000,               the balance at 00:00 IST on the 1st
    "credits_paise": 200000,              Σ positive entries in the month
    "debits_paise": -69000,               Σ negative entries (signed, as on entries)
    "closing_paise": 181000,              opening + credits + debits; for the current month, now
    "entries": [ Entry & { "balance_after_paise": 181000 } ],   §3.1 Entry, newest first, every
                                          entry of the month (no pagination: a household's month
                                          is a few dozen entries)
    "generated_at": "…" }
  422 "billing.period_invalid"           not YYYY-MM, or a month after the current one (IST)
```

- **From the ledger only.** Balances are sums of postings to `wallet:<user>`; `balance_after_paise` is the running balance after that entry, in posting order. A month falls by the entry's `at` in IST. A month before the wallet existed answers zeros and `[]`.
- **Not in it:** Kindness points (a separate currency, M9) and anything that did not post to the wallet (an unpaid or expired top-up).
- **Later:** a PDF of the same data (`format=pdf`) after MS-B. **Partners' earnings and payout statements** use this endpoint with `account=earnings` (§3.5, with M8 in S6); without it a partner gets their customer wallet.

| Mock today (`mocks/billing.js`) | Contract |
|---|---|
| Same top-level fields | Same, plus `currency` and `generated_at` |
| Entries without a running balance | `balance_after_paise` on each entry |
| A month chosen by `ref` (the delivery date) or `at` | By `at` in IST only; `ref` is the §3.1 object |

### 3.3 Refund to source contract (drafted 2026-10-09, frozen on approval)
This is the closed-loop rule "unused balance goes back only to the original payment source" (ADR-0004, handbook 13 §13.7). It is also how M1 closes a deleted account's wallet (M1 §3.5). The Wallet screen's "Refund my balance" and the backend build against this. Same sign-in, money, time and review rules as §3.1. **The API never calls Razorpay inside a request**: the request posts the ledger entries and queues the refund, and the `send_refunds` job makes the calls.

```
WalletOut  gains  "refundable_paise": 41000      what POST /v1/wallet/refund-to-source can send back
                                                  now (additive to §3.1)

RefundOut : {
  "id": "<uuid>", "amount_paise": 41000,
  "reason": "customer" | "account_deleted" | "order",   "order_id": "<uuid>" | null,
  "status": "pending" | "processing" | "processed" | "partly_failed" | "failed",
  "parts": [ { "topup_id", "razorpay_payment_id": "pay_…", "amount_paise": 20000,
               "status": "pending" | "sent" | "processed" | "failed",
               "razorpay_refund_id": "rfnd_…" | null } ],
  "created_at", "settled_at": "…" | null }

POST /v1/wallet/refund-to-source        customer; Idempotency-Key required
  request : { "amount_paise": 41000 | null }     null = everything refundable; at least ₹1 (100)
  202 RefundOut                          the same key replays the same answer
GET  /v1/wallet/refunds/{id}             200 RefundOut | 404 "billing.refund_not_found"
GET  /v1/wallet/refunds?cursor=&limit=   200 Page[RefundOut], newest first

billing.api.close_wallet(session, user_id, reason="account_deleted") -> RefundOut | None   (M1 §3.5)
billing.api.wallet_closed(session, user_id) -> bool        balance 0 and no part pending or sent
```

**Which top-ups take the money back.**
- **Newest first.** The amount is split over the user's `paid` Razorpay top-ups, newest first. Each top-up can take back at most its amount minus what earlier parts already took (failed parts don't count).
- **Within Razorpay's refund window.** Only top-ups captured within `RAZORPAY_REFUND_WINDOW_DAYS` qualify (default 180, Razorpay's six months; measured from the top-up's order, which is never later than its capture).
- **One part per top-up**, and one Razorpay refund per part.
- **`refundable_paise`** is the smaller of the balance and the sum of what the qualifying top-ups can still take.
- **Dev top-ups** (`provider: dev`) are refundable in dev and test, and the job settles their parts at once (`rfnd_dev_…`).

**Ledger** (amounts in paise; legs as in §4.1). The account and kind names here are new:

| Moment | Entry kind (key) | Debit | Credit |
|---|---|---|---|
| Request, per part | `refund_to_source` (`rts:<part id>`) | wallet:U | razorpay_clearing |
| `refund.failed` for a part | `refund_to_source_failed` (`rts_failed:<part id>`) | razorpay_clearing | wallet:U |
| Deletion: balance no top-up can take | `unclaimed` (`unclaimed:<user id>`) | wallet:U | `unclaimed_balances` (platform liability, new) |
| Deletion: a failed part no other top-up can take | `unclaimed` (`unclaimed:<part id>`) | wallet:U | `unclaimed_balances` |

- **Holding the cap.** A part counts against the cap while it is `pending` or `sent`, as unpaid top-ups do (`topup_room_paise` = cap − balance − unpaid top-ups − parts in flight). Before order refunds to source, a failed part could therefore always go back into the wallet without passing ₹10,000. An order refund to source keeps the balance where it was while its parts are in flight, so that no longer holds: **any** failed part (a customer's, an order's or a deleting account's) whose money would leave the wallet above ₹10,000 is moved once to another top-up that can take it, otherwise to `unclaimed` (`unclaimed:<part id>`); a deleting account's failed parts are moved that way whether they fit or not, as before.
- **On a deleting account,** a failed part is moved once to the next top-up that can take it, and otherwise to `unclaimed`. The customer can still claim it through support, and the money stays a liability, never revenue.
- **Unclaimed balances** (owner, 2026-10-09). Wallet money that cannot go back to a source goes to `unclaimed_balances` as above. Prof. Kranti Bade confirms this rule against RBI's closed-wallet (closed-system PPI) rules before the pilot.

**`send_refunds`** (every 5 minutes, cron-job.org) handles parts that are `pending`, in batches of 50:
1. **It calls Razorpay's refund API** with `amount`, `speed: "normal"` and `receipt: <part id>`, using our httpx2 client (the existing `RazorpayClient.refund` gains `receipt`).
2. **The part becomes `sent`** with the `rfnd_…` id.
3. **Before resending,** a part that was tried without an answer (a timeout or a crash after the call) first lists the payment's refunds and matches the `receipt`. A lost response is therefore never refunded twice.
4. **Retries.** It retries after 5 and 30 minutes, then reports the part in its summary as `stuck` for reconciliation (§3.4). It never marks a part failed without Razorpay saying so.

**Webhook** (the existing endpoint and dedupe, §3.1):
- `refund.processed` sets the part to `processed`.
- `refund.failed` sets it to `failed` and posts the `refund_to_source_failed` entry.
- When every part is final, the refund is `processed` (all its money went back, a moved part included), `failed` (none of it) or `partly_failed`, and `billing.refund_to_source_settled { refund_id, user_id, order_id, amount_paise, processed_paise, status }` (`order_id` null except for an order refund, below) goes to M11 (category money, link `/wallet`).
- New outcomes: `refund_processed`, `refund_failed`, `unknown_refund`. Refund events for the late over-cap refunds of §3.1 answer `already_settled`.

**Rules**
- **One refund in progress.** A user has at most one refund that is not final: another request gets 409 `billing.refund_in_progress` with `refund_id`. There are at most 3 requests per IST day (429 `billing.too_many_refunds`).
- **No guard on the next box.** A refund may leave the wallet short for an approved box: M5 cannot see orders (M4 depends on M5, not the reverse). The app shows the next box's total next to the button, and the cut-off then marks the order `payment_failed` as usual.
- **Statements.** The wallet statement (§3.2) shows `refund_to_source` (negative) and `refund_to_source_failed` (positive) entries with `ref: { "type": "refund", "id" }`.

**Error codes** (in addition to §3.1)

| Status | Code | When |
|---|---|---|
| 422 | `billing.refund_too_small` | Under ₹1 |
| 422 | `billing.refund_exceeds_refundable` | More than `refundable_paise`; the problem carries it |
| 409 | `billing.refund_in_progress` | An earlier refund is not final (`refund_id`) |
| 429 | `billing.too_many_refunds` | A fourth request in an IST day |
| 404 | `billing.refund_not_found` | Unknown refund, or someone else's |

#### Order refunds when the wallet is full (added 2026-10-21, frozen on approval)
Before this, a refund for an order that would take the wallet past ₹10,000 raised `billing.wallet_limit` and moved nothing, so the customer's money waited. That affected M4's shortages, issue credits, moves to `refunded` and carry-over expiry, M6's lost kept items, and M7's triage (M7 §10). Now `refund_to_wallet` sends such a refund to the payment source itself. The signature, the key and the return value are unchanged, so **no caller changes**.

```
refund_to_wallet(session, user_id, order_id, amount_paise, reason) -> JournalEntry   (unchanged)
  fits under the cap                        → the `refund` entry, as before
  does not fit, the top-ups can take it all → the `refund` entry, plus a Refund (reason "order")
                                              whose parts send the same amount back (below)
  does not fit, the top-ups cannot          → 422 billing.wallet_limit { room_paise }, nothing moves

RefundOut  gains  "order_id": "<uuid>" | null       set for reason "order"
           reason gains "order"
billing.refund_to_source_settled  gains  order_id (null for wallet refunds)
```

- **When.** It applies when balance + parts in flight + `amount_paise` would pass the cap, which is the same test as today. The **whole** refund then goes to the source, even when part of it would fit, so that one refund lives in one place.
- **In one transaction, under the wallet's row lock:**
  1. **The `refund` entry** (unearned_revenue → wallet) is posted as today. Every reader of order refunds (M4's `refunded_paise`, the statement's order refund line) stays as it is. Its `details` gain `"to_source": "<refund id>"`.
  2. **A Refund** with `reason: "order"` and `order_id` is created. It is split over the user's paid top-ups the same way as above: newest first, within `RAZORPAY_REFUND_WINDOW_DAYS`, one part per top-up.
  3. **One `refund_to_source` entry per part** (wallet → razorpay_clearing) is posted, so the wallet's balance ends where it started. The parts are sent by `send_refunds` and settled by the webhook like any other part.
- **All or nothing.** If the top-ups cannot take the whole amount (they are older than the window, or already refunded), or the amount is under ₹1, the call raises `billing.wallet_limit` as before and posts nothing. The callers' existing handling (an issue left open, a row left `pending`, a triage attempt that waits) stays as the rare fallback.
- **Once per order and reason.** The same call again finds the `refund:<order>:<reason>` entry and returns it. It creates no second Refund; the Refund's `idempotency_key` is `order:<order id>:<reason>`.
- **Not a customer request.** The one-refund-in-progress and three-a-day rules don't apply, as for `close_wallet`. A customer's own request may run beside an order refund.
- **A failed part** (`refund.failed`), of this refund or of any other, is handled as follows:
  - Its money goes back into the wallet (`refund_to_source_failed`) when it fits under the cap.
  - When it doesn't fit, the money is moved once to another top-up that can take it, otherwise to `unclaimed` (`unclaimed:<part id>`). This is the same rule as for a deleting account, and it applies to a customer's own refund too ("Holding the cap" above). For example, an order refund goes to source on a full wallet; the customer then refunds most of their balance; both refunds fail. Without the rule, the wallet would end above the cap.
  - The money never stays in a wallet above ₹10,000.
- **What the customer sees.**
  - The wallet statement shows the order refund (positive, `ref` the order), followed by the `refund_to_source` parts (negative, `ref` the refund).
  - `GET /v1/wallet/refunds` lists the refund with `reason: "order"` and its `order_id`.
  - When it settles, `billing.refund_to_source_settled` tells M11. M11 says "₹X for your box went back to your UPI/card" and links to `/wallet`.
- **Tests.**
  - Fits → wallet, as before.
  - Wallet full → one Refund with parts newest first and a balance unchanged.
  - The same call twice → one Refund.
  - Top-ups too old → `billing.wallet_limit` and nothing posted.
  - A failed part on a full wallet goes to the next top-up, then to `unclaimed`.
  - An order refund beside a customer's open refund is allowed.
  - Each caller's wallet-full path refunds to the source end to end:
    - an M4 shortage;
    - an M4 issue credit;
    - an M4 move to `refunded`;
    - M4's `expire_carry_overs`;
    - M6's `lost`;
    - M7's triage `refund`.

*As built (2026-10-21).*
- M11 has no consumer for `billing.refund_to_source_settled` yet (a customer's own refund has none either), so the "went back to your UPI/card" message waits for it. The event now carries `order_id` (null for a customer's own refund or a deleting account's).
- The customer's one-refund-in-progress check ignores order refunds, so their own request runs beside one, as written above.
- `refund_to_wallet` reads the refund window from `get_settings()`, as it has no settings argument.

### 3.4 Reconciliation contract (drafted 2026-10-09, frozen on approval)
US-7 and handbook 13 ("an hourly job reconciles our records with Razorpay"). The console's Reconciliation page (admin) builds against this.

**`reconcile_payments`** runs hourly at :17 (GitHub Actions; the advisory lock and `scheduled_for` follow M0).
- **Window.** It covers Razorpay payments and refunds created in the 72 hours before `scheduled_for`, plus every item still open. Razorpay's lists are paged: `GET /v1/payments?from&to&count=100&skip`, and refunds per payment.
- **Read-only towards Razorpay.** Everything is read (the two lists, then single payments for top-ups credited in the window or named by open items, and a payment's refunds for parts `sent` but not listed) before anything is written: when Razorpay is unavailable the run is `failed` and changes nothing.
- **Dev top-ups are skipped.** Without Razorpay keys the run is `skipped`.

| Check (`kind`) | What it compares | What the job does |
|---|---|---|
| `captured_not_credited` | A captured payment for one of our top-up orders, whose top-up is not `paid` (a lost webhook) | **Heals**: runs the same settle path as the webhook (idempotent by `topup:<payment id>`); item `healed`. A payment that no longer fits under the cap would need a refund, which this job never sends: its item stays open for an admin. A second captured payment on an already paid order also opens this kind |
| `credited_not_captured` | A `topup` entry whose payment is not captured at Razorpay | Opens an item (critical) |
| `amount_mismatch` | Payment amount or currency ≠ the top-up's | Opens an item (the webhook already refused it) |
| `unknown_payment` | A captured payment whose order is not ours | Opens an item |
| `refund_status_mismatch` | A part `sent` here but `processed` or `failed` at Razorpay (a lost refund webhook) | **Heals** by applying the same processing as the webhook |
| `refund_unknown` | A Razorpay refund on one of our payments that we have no part or over-cap refund for | Opens an item (critical: money left without a ledger entry) |
| `refund_stuck` | A part still `pending` or `sent` after 7 days | Opens an item |

```
ReconItem : { "id", "kind", "status": "open" | "healed" | "resolved",
              "razorpay_ref": "pay_…" | "rfnd_…", "topup_id" | null, "refund_part_id" | null,
              "user_id" | null, "expected_paise" | null, "actual_paise" | null,
              "first_seen_at", "last_seen_at", "resolution": { "by", "note", "at" } | null }

GET  /v1/admin/reconciliation?status=open&kind=&cursor=&limit=     admin (reconciliation:read)
  200 { "items": [ReconItem], "next_cursor", "last_run": { "scheduled_for", "status", "checked",
        "healed", "opened" } }
POST /v1/admin/reconciliation/{id}/resolve     admin (reconciliation:resolve); { "note": "…" } (1 to 500) → 200 ReconItem
  409 "billing.recon_not_open" · 404 "billing.recon_not_found"
```

- **One open item per (kind, Razorpay ref).** A mismatch seen again only moves `last_seen_at`.
- **Resolving records a decision; it moves no money.** A money correction is a separate `adjustment` entry. Until the pilot it is an audited manual fix (owner, 2026-10-09): an admin writes the `adjustment` entry by hand under the ledger rules (§4.1: postings sum to zero, a new entry, never an update) and records the reason and the item in `platform.audit_log`. There is no admin endpoint for it.
- **Alerting.** A run that opens an item publishes `billing.reconciliation_mismatch { run_id, opened, kinds }`. M11 sends it to admins when admin messages exist; until then it appears in the job's summary and on the page.

### 3.5 Partner earnings and payouts contract (drafted 2026-10-09, frozen on approval)
US-6 and handbook 05 §5.7: earnings accrue in the ledger when the work is done, and a weekly job pays out what is owed. **M8 decides what a piece of work earns**: it owns the rate card and knows the partner and the assignment. **M5 records the money and pays it out.** The partner Earnings screens and M8's `GET /v1/earnings?week=` read M5. Ships with M8 in S6.

```
billing.api.accrue_earning(session, *, partner_id, basis, ref_type, ref_id, units, amount_paise,
                           earned_at) -> entry_id
    basis: a basis of M8's rate card: "tiffin_portion" | "bag_delivered" | "dispatched_order" |
    "packed_order" (M8 §3.6); ref_type "order"; units (portions, bags or orders) and basis are kept
    on the entry for the earnings view. One "earning" entry (fulfilment_expense →
    partner_payable:<partner>), key earning:<partner_id>:<basis>:<ref_id>, so a replayed event never
    pays twice. M8 calls it in its consumers of M4's fulfilment events, in the consumer's
    transaction: e.g. orders.delivered with actor "captain" (₹6 a bag; M4 §3.7 events carry
    actor_user_id) or orders.delivered of a tiffin (the kitchen's portions)
billing.api.reverse_earning(session, *, partner_id, basis, ref_id, reason)   "earning_reversal", the
    opposite legs, key earning_reversal:<partner_id>:<basis>:<ref_id>; at most what was accrued.
    M8 does not call it in v1 (a refund does not claw back an earning, M8 §3.6); for corrections
billing.api.partner_week(session, partner_ids, week) -> PartnerWeek          for M8's GET /v1/earnings
    PartnerWeek : { "from", "to", "total_paise",       ISO week, Monday–Sunday IST, by earned_at
                    "by_basis": [ { "partner_id", "basis", "units", "amount_paise" } ],
                    "days": [ { "date", "amount_paise" } ],   7 entries
                    "entries": [ { "id", "partner_id", "basis", "units", "amount_paise",
                                   "ref": { "type", "id" }, "at" } ],
                    "payouts": [ PayoutOut ] }          that week's payouts of those partners
billing.api.partner_rolling_earnings(session, partner_ids, basis, since) -> { partner_id: paise }
    earnings less reversals since a date, for M8's fair assignment (M10's earnings_paise)

PayoutOut : { "id", "partner_id", "week": "2027-W03", "period": { "from", "to" },   IST Monday–Sunday
              "amount_paise": 84000,
              "status": "held" | "pending" | "processing" | "processed" | "failed",
              "hold_reason": "kyc_incomplete" | "suspended" | "no_payout_account" | null,
              "provider": "simulated" | "razorpay_route", "provider_ref": "pout_sim_…" | "trf_…" | null,
              "created_at", "processed_at" | null }

GET  /v1/payouts?cursor=&limit=                partner (own, X-Active-Role a partner role)  Page[PayoutOut]
GET  /v1/statements?period=2027-01&account=earnings
                                               partner (own): the §3.2 Statement over partner_payable;
                                               entry kinds earning, earning_reversal, payout,
                                               payout_reversed; ref { "type": "order" | "payout", "id" }
GET  /v1/admin/payouts?week=&status=&hub_id=&cursor=    admin, hub_manager (hub)   Page[PayoutOut & { "partner": { "display_name", "kind" } }]
POST /v1/admin/payouts/{id}/retry              admin; Idempotency-Key; a held or failed payout once
                                               the reason is fixed → 200 PayoutOut (pending)
```

**`weekly_payouts`** (Monday 10:00 IST; advisory lock; the weekly statement is the partner's `GET /v1/statements?account=earnings` above, so no separate statement job runs):
1. **What is owed.** For each partner, the balance of `partner_payable:<partner>` counts entries up to Sunday 23:59:59 IST. Anything earned later is paid the next week.
2. **The minimum.** A balance under ₹100 is carried forward with no row, except for a partner who `left` (account deletion, M1 §3.5): her whole balance is paid, so the purge's "partner payable is 0" precondition can be met.
3. **Eligibility.** `partners.api.payout_eligibility(session, partner_id) -> { eligible, reason }` (M8 §3.7), `reason` one of `kyc_incomplete`, `suspended`, `no_payout_account` or null: the partner is `active`, `paused` or `left` (a partner who left is still paid what she earned; a `suspended` one is held), its KYC documents are accepted, and its `bank` document is accepted with a passed penny drop. If not eligible, a `held` row records the reason and nothing is posted; the next run, or a retry, tries again. The account details come only from `partners.api.payout_account(session, partner_id)` (it decrypts and is audit-logged); only a real provider calls it, never the simulated one.
4. **Eligible.** A `pending` row is created, and the `payout` entry (partner_payable:P → payout_clearing, key `payout:<partner>:<week>`) is posted in the same transaction. A rerun of the job finds the key and never pays twice.
5. **The provider is called** after commit, within the same job:
   - `simulated` answers `processed` at once with `pout_sim_<id>`. It is the only provider the prototype builds (owner, 2026-10-09);
   - `razorpay_route` (for the pilot, not built in the prototype; test mode, behind `PAYOUTS_PROVIDER`) would make a direct transfer (`POST /v1/transfers { account: "acc_…", amount, currency: "INR", notes }`) and waits for its webhook (`transfer.processed` / `transfer.failed`; check the event names when this is built).
6. **A failure** posts `payout_reversed` (payout_clearing → partner_payable:P), sets `failed`, and leaves the money for the next run or a retry.
7. **`billing.payout_sent { payout_id, partner_id, amount_paise, status }`** is published on `processed` and `failed`, for M11's partner messages.

- **Tax.** No TDS is deducted in the prototype (§11).
- **Earnings mirror.** M8 keeps no `partners.earnings` table: it reads `partner_week` (M8 §3.6).
- **As built (earnings, with M8 §3.6).** `accrue_earning`, `partner_week` and `partner_rolling_earnings` are in `billing.api` (`billing.earnings`); the `earning` entry kind and the platform account `fulfilment_expense` come with the migration `…_billing_earnings`. The basis, the units, the partner and `earned_at` (UTC) are kept in the entry's `details`, and the views read the week and the rolling total from them by `earned_at` (IST week, Monday to Sunday). `accrue_earning` takes an optional `occurrence` (M8 passes the order event of the trip) that is appended to the key, for work that can repeat on one order; it takes positive units and amounts only (a rate of 0 is M8's to skip) and returns the entry's id, the same one on a replay. `PartnerWeek.payouts` is `[]` until `weekly_payouts` exists; `reverse_earning`, `PayoutOut`, `payout_eligibility` and the other entry kinds (`earning_reversal`, `payout`, `payout_reversed`) are not built yet.
- **As built (payouts, 2026-10-25).** `weekly_payouts`, `billing.payouts`, the entry kinds `payout` and `payout_reversed` (`ref_type` `payout`) and the platform account `payout_clearing` (asset) come with the migration `…_billing_payouts`; `partners.api.payout_eligibility` and `payout_account` with M8 (M8 §3.7, as built). `reverse_earning` and `earning_reversal` are still not built (nothing calls them in v1). Interpretations:
  - **The week and what is owed.** The run pays the ISO week before the IST week of its `scheduled_for` (a late or manual run in the same week pays the same week). The balance counts an `earning` by its `earned_at` (as `partner_week` does, so a fresh partner's payout equals her earnings week) and any other entry by when it was posted, before Monday 00:00 IST; every `payout` and `payout_reversed` entry counts whenever it was posted, since each settles money owed before its own week ended.
  - **Earlier held or failed payouts.** Step 3's "the next run tries again" is read as: the run first tries each of the partner's `held` and `failed` rows again, a later week's too (paid when she is now eligible, else held with the current reason), and a new row takes only what is owed beyond the rows still waiting. So each row keeps its amount, one week's money is never in two rows, and a later retry of an old row can never pay twice. A late run for a missed week counts the rows of later weeks as waiting too, so it never holds money a later row already holds. The ₹100 minimum applies to that new amount.
  - **Attempts.** The first `payout` entry of a row is keyed `payout:<partner>:<week>` as written; a retry after a failure posts a new one, `payout:<partner>:<week>:<n>`, and its reversal `payout_reversed:<partner>:<week>[:<n>]`, so every attempt moves money exactly once. `attempts` counts them; `entry_id` is the latest.
  - **Statuses.** The simulated provider goes `pending → processed` (or `failed`) at once; `processing` is kept for a real provider waiting for its webhook and publishes nothing. The provider is called after the rows are committed, one transaction per payout (`FOR UPDATE SKIP LOCKED`), so a crash leaves a row `pending` and the next run (or a retry) sends it. `provider_ref` is `pout_sim_<payout id hex>`.
  - **A failure** (only a test's provider fails; the simulated one never does) posts `payout_reversed`, sets `failed` with the provider's ref, and publishes `billing.payout_sent` with `status: "failed"`. The money is back in `partner_payable` for the next run or a retry.
  - **The table.** `billing.payouts` has §4's columns plus `hub_id` (the partner's hub when the row was made, for a hub manager's list and the `hub_id` filter), with checks that a `held` row has a reason and that a pending, processing or processed row has its entry.
  - **Guards.** A partner's payout decisions run under her `partner_payable` row lock (the job, and the retry endpoint), and so does a provider failure's `payout_reversed` (a run never sees the money back while the row still looks sent), and a `payout` entry is never larger than her balance (409 `billing.payout_exceeds_payable`; only possible once earnings can be reversed). A payable with no partner record (data error) is counted as `failed` in the summary and paid nothing. The summary has `week`, `pending`, `held`, `carried` (under ₹100, no row), `existing` (a rerun), `retried`, `failed` (a partner whose savepoint failed), then the provider's `processed`, `processing`, `refused` (answered failed) and `send_failed`.
  - **`PAYOUTS_PROVIDER`.** `simulated` (the default) or `razorpay_route`; the latter fails at start with a configuration error ("not built in the prototype, pilot only"), so `payout_account` is never called yet.
  - **Endpoints.** `GET /v1/payouts` needs `payouts:read` through a partner role (kitchen_partner, captain, rider, packer; with `X-Active-Role`, that role) and lists every partner record of the caller, as `GET /v1/earnings` does. `GET /v1/admin/payouts` is admin (every hub) or hub_manager (her hubs; another `hub_id` is 403); a partner role gets 403. `week` is optional (every week, newest first) and is validated (422 `billing.week_invalid`). `PayoutOut` gains `hub_id` (the console's mock has it). `POST …/retry` (admin, `payouts:write`, audit `billing.payout_retried`) answers the `pending` payout it committed, then runs the provider step in the same request; a `GET` right after shows `processed`. Errors: 404 `billing.payout_not_found`; 409 `billing.payout_not_retryable` (with `payout_status`) for a payout that is not held or failed; 409 `billing.payout_still_held` (with `hold_reason`) while M8 still holds her.
  - **Statement.** `GET /v1/statements?period=&account=earnings` (`statements:read` for the partner roles) is §3.2's statement over all of the caller's `partner_payable` accounts as one running balance, with `"account": "earnings"`, entry kinds `earning`, `payout` and `payout_reversed`, and `ref` `{ type: "order" | "payout", id }`; a month falls by when the entry was posted, as in §3.2. The wallet statement (no `account`, or `account=wallet`) now needs the customer role explicitly, since partner roles also hold `statements:read`.
  - **Earnings view.** `PartnerWeek.payouts` is the week's rows of those partners, and M8's `GET /v1/earnings` shows them as `PayoutOut & { kind }`.

### 3.6 Kindness points contract (drafted 2026-10-09, frozen on approval)
Kindness points are a separate currency (`KINDNESS_PTS`), never money and never cash (ADR-0004, handbook 06 §6.7). The mobile Wallet and Kindness screens (`useKindness`, `mocks/billing.js`) and M9's donation verification build against this. Ships with M9 donations in S9. **Points are redeemed only as donated meals** (owner, 2026-10-09, below): they have no cash value and never come off a box or into the wallet.

```
GET /v1/kindness/balance       customer; Cache-Control: no-store
  200 { "points": 340,
        "expiring": [ { "points": 120, "on": "2026-12-31" } ],   IST dates, soonest first, the next
                                                                 3 dates within 90 days; [] if none
        "goal": { "points": 500, "code": "child_meal" } | null } the points needed for the next
                                                                 donated meal (KINDNESS_GOAL, 500);
                                                                 null when unset
GET /v1/kindness/entries?cursor=&limit=     Page[{ "id", "kind": "points_earned" | "points_expired"
                                                               | "points_donated",
                                            "points": 120 (signed), "ref": { "type": "donation", "id" } | null,
                                            "expires_on": "…" | null, "at" }]   newest first

billing.api.credit_points(session, user_id, points, source_type, source_id) -> JournalEntry
    "points_earned": kindness_expense (PTS) → kindness:<user> (PTS), key points:<source_type>:<source_id>;
    one lot that expires 365 days after the credit (IST end of day)

POST /v1/kindness/donations    customer; Idempotency-Key required
  request : { "meals": 1 }       1 to 10
  201 : { "id", "meals": 1, "points": 500, "code": "child_meal", "at" }   id: the journal entry
  409 "billing.not_enough_points"   fewer points than meals × goal.points (carries "points" and "needed")
```

- **Credited by M9's events.** A consumer of `community.donation_verified { donation_id, user_id, points }` calls `credit_points(…, "donation", donation_id)`. Later sources (a referral reward, if M9 pays it in points) use the same facade with their own `source_type`. Each credit publishes `billing.points_credited { user_id, points, source_type, source_id, expires_on }` for M11 (category `kindness`).
- **Expiry, oldest lot first.** `expire_kindness_points` (daily 00:30 IST) posts `points_expired` (kindness:U → kindness_expense, key `points_expiry:<lot id>`) for each lot past `expires_at` with points left.
- **Lots.** `billing.points_lots` keeps the expiry schedule (`remaining`). The balance is still the sum of postings to `kindness:<user>`. A property test asserts that Σ `remaining` = the ledger balance.
- **Not in money views.** Points never appear in `WalletOut`, wallet entries or statements (§3.2), and the ₹10,000 cap does not apply to them.
- **Redeeming is donating a meal** (owner, 2026-10-09). The only way to use points is "a meal for a child", given through M9's partners: no cash value, no rupees off a box, no wallet credit. **`goal.points`** is the points one donated meal takes (`KINDNESS_GOAL`, 500), so it is what the customer needs for the next donated meal; the app shows `points` against it and enables "Donate a meal" once `points ≥ goal.points`. `POST /v1/kindness/donations` posts one `points_donated` entry (kindness:U → kindness_expense, PTS, key `points_donation:<user id>:<Idempotency-Key>`) for `meals × goal.points`, taken from the lots oldest first like expiry, and publishes `billing.points_donated { entry_id, user_id, meals, points, code }`; M9 consumes it to count the meals towards its NGO partners' hand-offs and the impact counters (M9 follow-up, §10). No INR account is ever touched.
- **Expiry stays at 365 days** (owner, 2026-10-09).
- **As built (S9, the credit side, for M9's referrals).** `credit_points` (`billing/kindness.py`), the `billing.points_lots` table, the platform account `kindness_expense` (KINDNESS_PTS), the entry kinds `points_earned`, `points_expired` and `points_donated`, and `billing.points_credited`. The journal entry's `ref_type` is `donation` or `referral` (`referral` is added beside §4.1's list for M9 §3.3), and `details` keeps the `source_type`. A repeated credit returns the posted entry and publishes nothing; the same source with another amount is 409 `billing.entry_conflict`. The lots are in M5's data export (`kindness_points`).
- **As built (S9, the rest).** `GET /v1/kindness/balance` counts only lots that have not expired, so a lot past `expires_at` stops counting at once, before `expire_kindness_points` takes it back at 00:30; `goal` is always set (`KINDNESS_GOAL`, default 500). `GET /v1/kindness/entries` gives `points_earned` and `points_expired` the lot's source as `ref` (`donation` or `referral`) and `points_donated` no `ref`. `POST /v1/kindness/donations` checks the points of unexpired lots only; a client key that would make the ledger key longer than 160 characters is hashed (`points_donation:<user id>:sha256:<hex>`). `expire_kindness_points` uses its scheduled time, not the clock, and posts nothing twice for a lot. The consumer of `community.donation_verified` (subscribed by name; `billing/jobs.py`) credits the points once per donation; a payload without a donation id, a user id or positive whole points, or the same donation with other points, is logged and skipped, never raised. Permissions `kindness:read` and `kindness:donate` (customer). Owner's 365-day expiry kept; nothing about Kindness points moves money.

| Mock today (`mocks/billing.js`) | Contract |
|---|---|
| `{ points, goal: { points, code }, expiring: [{ points, on }] }` | Same fields; `goal` may be `null`, `expiring` may be `[]` (the app already falls back to `pointsNoteNoExpiry`) |

### 3.7 UPI Autopay mandates: cut-able, not in this phase
UPI Autopay is third in the plan's cut order (handbook 14 §14.3) and is planned for the pilot phase only. No contract is frozen now. Until it is:
- `POST /v1/subscriptions` with `payment_mode: "autopay"` keeps answering 422 `subscriptions.payment_mode_unavailable` (M3 §3.1).
- `/v1/mandates` is not served and stays out of `openapi.json`.
- `billing.mandates` is not created.
- The onboarding payment step (`routes/_auth/onboarding/payment.jsx`) should hide or disable the Autopay choice behind a flag, rather than call `POST /v1/mandates`; the mock may stay for design reviews.

If the pilot keeps Autopay, its contract follows handbook 06 §6.5 (the mandate states, the 24-hour pre-debit notice at T-3 18:30, the capture at cut-off, `CAPTURED → POSTED` as the only ledger write) and is written at the start of P6.

### 3.8 Order refunds list (drafted 2026-10-25, frozen on approval)
The console's Refunds page (`(admin)/refunds`) builds against this, as a read-only list of the money that went back for orders. Today it calls `GET /v1/admin/refunds?status=pending` as a review queue with approve, reject and reopen (`mocks/billing.js`). Order refunds are automatic, though: shortages, issue credits, moves to `refunded`, carry-over expiry and lost kept items all post at once through `refund_to_wallet` (§3.3). So there is nothing to approve. A disputed delivery goes through M4's issue triage (M4 §3.6).

**Who serves it.** M5 does not read orders or households: M4 and M1 depend on M5, not the reverse (§3.3, Rules). The list is per hub, society and flat, so **M4's router serves the path** (M4 §3, §7). It joins its own orders to the rows of the new M5 facade below. The money fields are M5's.

```
billing.api.order_refunds(session, order_ids) -> list[OrderRefund]      new
  OrderRefund : { entry_id, order_id, reason (the entry's code, e.g. "short_<hex>"), amount_paise,
                  refund_id | None, refund_status | None, created_at }
                every `refund` journal entry whose ref is one of the orders; refund_id and its
                status when the entry's details carry "to_source" (a full wallet, §3.3)

RefundRow : {
  "id": "<journal entry id>",
  "order_id", "order_kind": "box" | "addon" | "tiffin", "delivery_date",
  "society": { "id", "name" }, "flat_label": "B wing, B-402" | null,   as M6 §3.2; never a name or phone
  "reason": "shortage" | "issue" | "undelivered" | "no_kitchen" | "carry_over" | "other",
  "product_id": "<uuid>" | null,          shortage and carry_over: the line's product
  "issue_id": "<uuid>" | null,            issue: the order issue (M4 §3.2)
  "amount_paise": 4000,
  "to": "wallet" | "source",
  "refund_id": "<uuid>" | null,           to source: the Refund that sends it back (§3.3)
  "status": "credited" | "pending" | "processing" | "processed" | "partly_failed" | "failed",
                                          credited for the wallet; the Refund's status for a source
  "created_at" }

GET /v1/admin/refunds?hub_id=&from=&to=&reason=&destination=&cursor=&limit=
    hub_manager (hub), admin (order_refunds:read, M4 §7). Cache-Control: no-store
    hub_id: required for hub staff (their hub), optional for admins (every hub)
    from, to: delivery dates (IST), default the last 7 days to today; at most 31 days apart
    reason: one of the six; destination: wallet | source; limit ≤ 100, default 50
  200 Page[RefundRow]   newest first (created_at, then id)
  403 "auth.forbidden" (another hub) · 422 "request.invalid" (a window over 31 days, an unknown value)
```

- **Reasons.** They map the entry's code: `short_<hex>` is `shortage`, `issue_<hex>` is `issue`, `carry_<hex>` is `carry_over` (expiry and lost kept items alike), `undelivered` and `no_kitchen` keep their names, and any other code is `other`. The hex part gives `product_id` or `issue_id`.
- **No money moves here, and no new state.** A wallet refund is final when posted (`credited`). A refund to source follows `send_refunds` and the webhook (§3.3), and a stuck part shows up in reconciliation (§3.4).
- **What hub staff do not see.** A household's wallet balance or top-ups, the customer's name or phone, and customers' own refunds to source (`reason` customer or account_deleted): those are wallet matters, not order refunds.
- **New facade:** `billing.api.order_refunds`, above. It reads M5's own tables only, so M5 gains no dependency.

| Mock today (`mocks/billing.js`) | Contract |
|---|---|
| `GET /v1/admin/refunds?status=pending`, a queue of `pending` refunds | A read-only Page of what was refunded, filtered by hub, delivery dates, reason and destination |
| `flat`, `society` (strings) | `flat_label` and `society { id, name }` |
| `order_label: "Box · Tue 6 Oct"` | `order_kind` and `delivery_date`; the console words it |
| `why`, `what_happened`, `reported_by` | `reason` with `product_id` or `issue_id`; who reported an issue is on the issue (M4 §3.6) |
| `to: "wallet"` | `to: "wallet" \| "source"`, with `refund_id` for a source |
| `wallet_before_paise` | Not in the contract: hub staff do not see a household's balance |
| `status: "pending" \| "approved" \| "rejected"` | `credited`, or the Refund's own status |
| `POST /v1/admin/refunds/{id}/approve \| reject \| reopen` | Dropped: refunds are automatic (§3.3); disputes are M4's issue triage (M4 §3.6) |

**As built (the list, with the M4 PR).** `billing.api.order_refunds` and the `OrderRefund` row are in `billing/refunds.py`; the amount is the entry's debit leg and a missing Refund row reads `pending`. M4's `GET /v1/admin/refunds` (`orders/refund_list.py`) takes the delivery-date window's orders (the hub's, or every hub's for an admin), asks the facade in chunks of 2,000 ids, then filters by `reason` and `destination`, orders by (created_at, id) newest first and keysets the page on that pair, all in memory: the window is at most 31 days. A hub manager without `hub_id` gets 422 `orders.hub_required` (as the issue queue); `from` defaults to seven days before `to`, and `from` after `to` is 422 `request.invalid` too. A refund of an order whose code the table does not know (`returned_to_hub`, `left_behind`) is `other`. Permission `order_refunds:read` is registered in M4's `permissions.py`.

## 4. Tables owned
| Table | Key columns |
|---|---|
| `billing.ledger_accounts` | id, owner_type, owner_id, currency (INR, KINDNESS_PTS), kind (asset, liability, revenue, expense) |
| `billing.journal_entries` | id, kind, ref_type, ref_id, details jsonb (e.g. delivery_date, reason), idempotency_key (unique), created_at; append-only |
| `billing.postings` | entry_id, account_id, amount (signed minor units: debit +, credit −) — deferred trigger enforces Σ = 0 per currency; append-only |
| `billing.payments` | id, user_id, provider, provider_ref, amount_paise, status, purpose |
| `billing.mandates` | id, user_id, provider_ref, max_paise, status, last_notice_at — **not created in this phase** (§3.7) |
| `billing.payment_webhooks` | id, provider, event_id (unique), payload jsonb, processed_at |
| `billing.payouts` | id, partner_id, week (ISO, e.g. 2027-W03), period_from, period_to, amount_paise, status (held, pending, processing, processed, failed), hold_reason, provider (simulated, razorpay_route), provider_ref, entry_id, attempts, created_at, processed_at; unique (partner_id, week) |
| `billing.points_lots` | id, user_id, points, remaining, earned_at, expires_at, source_type, source_id, entry_id; unique (source_type, source_id) |
| `billing.refunds` | id, user_id, amount_paise, reason (customer, account_deleted, order), order_id (M4's order, for reason order; no foreign key across schemas), status, idempotency_key, created_at, settled_at (§3.3) |
| `billing.refund_parts` | id, refund_id, payment_id → payments, replaces_part_id (a failed part moved once: a deleting account's, or an order refund's that does not fit back), amount_paise, status (pending, sent, processed, failed), provider_refund_id (unique), attempts, next_attempt_at, last_error, entry_id, failed_entry_id, sent_at, settled_at |
| `billing.reconciliation_runs` | id, scheduled_for, window_from, window_to, status, checked, healed, opened, error, finished_at (§3.4) |
| `billing.reconciliation_items` | id, run_id, kind, status (open, healed, resolved), razorpay_ref, topup_id, refund_part_id, user_id, expected_paise, actual_paise, first_seen_at, last_seen_at, resolved_by, resolution_note, resolved_at; one open item per (kind, razorpay_ref) |

### 4.1 Ledger rules (handbook 05 §5.7)
- **Accounts:** one account per (owner, currency, purpose), created on first use: `wallet:<user>` (liability, INR) for customers; platform accounts `razorpay_clearing`, `unearned_revenue`, `sales_revenue` and `promo_expense` are seeded by the migration. Added by §3.3–§3.6: `kindness:<user>` (liability, KINDNESS_PTS), `partner_payable:<partner>` (liability, INR, owner type `partner`), and the platform accounts `unclaimed_balances` (liability), `payout_clearing` (asset), `fulfilment_expense` (expense) and `kindness_expense` (expense, KINDNESS_PTS).
- **Entry kinds** added by §3.3–§3.6: `refund_to_source`, `refund_to_source_failed`, `unclaimed`, `earning`, `earning_reversal`, `payout`, `payout_reversed`, `points_earned`, `points_expired`, `points_donated`; `ref_type` adds `refund`, `payout`, `donation`.
- **Entries:** a journal entry has two or more postings; a deferred constraint trigger rejects the transaction unless they sum to zero per currency. Entries are never updated or deleted; a mistake is corrected by a new `adjustment` entry.
- **Exactly once:** every entry has a unique `idempotency_key` built from what caused it (`topup:<payment id>`, `box:<order id>`, `refund:<order id>:<reason>`). Posting the same key again returns the existing entry.
- **Balances** are sums of postings, never a stored column. A posting to a wallet first locks that wallet's account row, so two debits at the same moment cannot both pass the balance check. The wallet never goes below zero or above the cap.
- **Tests:** Hypothesis property tests for these four invariants (§9), plus a test that a duplicate webhook delivery credits once.

## 5. Events
**Published:** `billing.wallet_topped_up`, `billing.payment_captured`, `billing.payment_failed`, `billing.refund_issued`, `billing.points_credited`, `billing.payout_sent`; added by §3.3–§3.5: `billing.refund_to_source_settled`, `billing.reconciliation_mismatch`; added by §3.6: `billing.points_donated`. (`payment_captured` and `payment_failed` are Autopay events, §3.7.)
**Consumed:** `orders.locked` (debit/capture request via api), `orders.delivered` (recognise revenue), `orders.delivery_failed`, `orders.shortage_substituted`, `community.donation_verified` (points, §3.6). M1's `DELETE /v1/me` calls `billing.api.close_wallet` in its own transaction (M1 §3.5), so there is no consumer for the deletion. Earnings are not consumed from events: M8 calls `billing.api.accrue_earning` (§3.5).

## 6. Jobs
| Job | Schedule | Notes |
|---|---|---|
| `process_webhooks` | Outbox consumer after the webhook is stored | Idempotent by event_id |
| `send_pre_debit_notices` | T-3 18:30 | Autopay only; not in this phase (§3.7) |
| `send_refunds` | Every 5 minutes (cron-job.org) | Sends pending refund-to-source parts to Razorpay; receipt-matched before any resend (§3.3) |
| `reconcile_payments` | Hourly at :17 | Razorpay payments and refunds vs our records; heals lost webhooks, opens items (§3.4) |
| `expire_kindness_points` | Daily 00:30 IST | Oldest lot first (§3.6) |
| `weekly_payouts` | Monday 10:00 IST | Partner payables up to Sunday 23:59 IST; eligible partners only; the simulated provider in the prototype (§3.5) |

Scheduled jobs run as internal job endpoints (`POST /v1/internal/jobs/<name>`) called by a GitHub Actions schedule (ADR-0021). Razorpay calls go through our own thin async client on httpx2, not the official SDK.

## 7. Permissions
| Permission | Roles (scope) |
|---|---|
| `wallet:read/write` | customer (own) |
| `statements:read` | customer (own wallet, §3.2); partner (own earnings, with M8); franchise_owner (hub aggregates, later) |
| `reconciliation:read` · `reconciliation:resolve` | admin |
| `refunds:write` | customer (own wallet, §3.3) |
| `order_refunds:read` | hub_manager (hub), admin (§3.8; registered and checked by M4, which serves the path) |
| `payouts:read` | partner (own), hub_manager (hub), admin |
| `payouts:write` | admin (retry) |
| `kindness:read` · `kindness:donate` | customer (own) |

## 8. Screens
**Mobile:** Wallet card + top-up, transaction list, Autopay setup, Kindness points (with M9 gullak), partner earnings and statements. **Console:** reconciliation, payouts, refunds review.

## 9. Tests required
- Property (hypothesis): every journal entry sums to zero per currency; wallet never negative; replaying an idempotency key never double-posts; points never redeemed after expiry.
- Integration: webhook signature verification; duplicate webhook delivery; reconciliation mismatch detection.
- E2E: top-up in Razorpay test mode inside the Capacitor app (Phase 1 spike).

- Leftovers (§3.3–§3.6): refund allocation newest-first within the window; a part counts against the cap while in flight, and a `refund.failed` that would leave the wallet above the cap (possible once an order refund went to source) is moved once to another top-up or to `unclaimed`; a lost Razorpay response is matched by `receipt` and never refunded twice; duplicate refund webhooks change nothing; reconciliation heals a lost top-up webhook exactly once and opens one item per mismatch however many runs see it; a rerun of `weekly_payouts` posts no second `payout` entry; a held partner gets nothing posted; points lots always sum to the `kindness:` balance, expire oldest first, and never touch INR accounts; a donation takes `meals × goal.points` from the oldest lots, is refused with fewer points, replays once per key, and publishes `billing.points_donated`; every new entry kind sums to zero per currency (the existing property tests extended).
## 10. Dependencies
M0, M1 (users), M4 (orders via events/api), M8 (payee KYC status via `partners.api`), M11 (notices). **M9 follow-up** (owner's decision of 2026-10-09 on Kindness points): consume `billing.points_donated` and count the donated meals towards its NGO partners' hand-offs and the impact counters (§3.6).

## 11. Open questions
- Monthly top-up limit (proposed ₹20,000 in §3.1) and the 30-minute top-up expiry: confirm with the guide before a real-money pilot.
- Top-up bonus rules (e.g. ₹2,000 → ₹2,100) and their accounting as promo expense.
- ~~Captain commission per order value for the pilot (proposed ₹5–8).~~ Decided by the owner (2026-10-09, M8 §3.6): ₹6 a bag, paid when the bag is delivered.
- **Leftovers (§3.5):** TDS on partner payouts (and whether partners are paid real money at all in the pilot, handbook 13 §13.8) needs the guide's answer before real money.
- **Decided by the owner (2026-10-09):**
  - ~~Leftovers (§3.3): what happens to wallet money that cannot go back to a source?~~ Decided: it goes to `unclaimed_balances` as drafted, and support pays it out on request; Prof. Kranti Bade confirms this against RBI's closed-wallet rules before the pilot.
  - ~~Leftovers (§3.4): is a hand-written SQL fix with an audit row acceptable for manual ledger corrections until the pilot?~~ Decided: yes, an audited manual fix until the pilot; no admin endpoint.
  - ~~Leftovers (§3.5): the rate card, and Razorpay Route or the simulated provider?~~ Decided: the rates are M8's as drafted there (kitchen ₹60 a portion, captain ₹6 a bag at `delivered`, rider ₹8 and packer ₹4 an order, editable per hub); a simulated payout provider is enough for the prototype.
  - ~~Leftovers (§3.6): how are points redeemed, does the 365-day expiry stand, and what does `goal` mean?~~ Decided: points are redeemed only as donations ("a meal for a child") through M9's partners, with no cash value and no rupees off a box; the 365-day expiry stands; `goal.points` is the points needed for the next donated meal (§3.6).
