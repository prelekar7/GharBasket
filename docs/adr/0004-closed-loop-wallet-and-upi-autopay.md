# ADR-0004 — Payments: closed-loop prepaid wallet (default) and UPI Autopay; KYC only for payees

- **Status:** Accepted
- **Date:** 2026-10-02
- **Decision ID:** D4

## Context
The weekly Autopilot box must be paid without the customer checking out every week. The original design ("auto-approve after 4 hours, then auto-debit") conflicts with RBI's e-mandate rules, which require a pre-debit notification at least 24 hours before each recurring debit. The team also asked whether a wallet would force customer KYC.

## Decision
Support **both** payment modes:
1. **Closed-loop prepaid wallet (default).** Top-up via Razorpay; spend only on GharBasket orders; no cash withdrawal; no user-to-user transfers; unused balance refunded to the original source; self-imposed ₹10,000 balance cap and monthly top-up cap. Under RBI's PPI framework, a closed-system PPI does not need RBI authorisation and **customers need no KYC** (phone OTP only).
2. **UPI Autopay (optional).** Variable-amount mandate via Razorpay; box and pre-debit notice sent at T-3 18:00; capture at the T-1 21:00 cut-off. The bank performs mandate KYC.

**KYC applies only to people we pay:** partners and captains (masked ID, PAN, bank penny-drop, FSSAI registration for kitchens), riders, franchise owners (business KYC). Handled in module M8 with a `pending → verified` gate; simulated in the prototype. Kindness points are a separate ledger currency with expiry, not money.

## Alternatives considered
- **Wallet only** — rejected: some customers prefer not to pre-load money.
- **UPI Autopay only** — rejected: harder sandbox, more edge cases, and no top-up bonus lever for retention.
- **Open/semi-closed wallet (cash-out, transfers)** — rejected: would require RBI authorisation and customer KYC.

## Consequences
- (+) Simple, legal default; top-up bonuses create float and retention.
- (+) Weekly cycle respects the 24-hour pre-debit rule by design.
- (−) Two payment paths in the billing module (M5) to build and test.
- (−) Legal wording must be confirmed with the guide or a CA before any real-money pilot.

## Related
[ADR-0006](0006-postgresql-centric-data-platform.md) (double-entry ledger) · Handbook [Chapter 13](../handbook/13-security-and-compliance.md), [Chapter 07](../handbook/07-workflows.md)
