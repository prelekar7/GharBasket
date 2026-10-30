import { formatRupees, parseDecimal } from "./money.js";

/** What a customer reported (contract `IssueOut.kind`), in the words the desk uses. */
export const KIND_LABEL = /** @type {Record<string, string>} */ ({
  missing: "Item missing",
  damaged: "Item damaged",
  quality: "Poor quality",
  wrong_item: "Wrong item",
  late: "Late delivery",
  other: "Other",
});

/** Why an issue was not credited on the spot (contract `IssueOut.open_reason`). */
export const OPEN_REASON = /** @type {Record<string, string>} */ ({
  photo_needed: "Waiting for a photo",
  over_auto_credit_limit: "Over the automatic credit limit",
  over_order_total: "Over the order total",
  wallet_limit: "Wallet is full",
  triage: "Needs a decision",
});

/** The order statuses of the timeline (contract `TimelineEvent.status`). */
export const STATUS_LABEL = /** @type {Record<string, string>} */ ({
  approved: "Approved",
  locked: "Locked at the cut-off",
  payment_failed: "Payment failed",
  cancelled: "Cancelled",
  picking: "Being picked",
  packed: "Packed",
  dispatched: "On the way",
  at_society: "At the society",
  delivered: "Delivered",
  undelivered: "Not delivered",
  redelivery: "Redelivery planned",
  refunded: "Refunded",
});

/** Who moved the order (contract `TimelineEvent.actor`: a kind of actor, never a person). */
export const ACTOR_LABEL = /** @type {Record<string, string>} */ ({
  customer: "Customer",
  system: "System",
  hub: "Hub",
  packer: "Packer",
  rider: "Rider",
  captain: "Society captain",
  kitchen: "Kitchen",
});

/** Plain words for the refusals of the resolve call (M4 §3.6). */
export const RESOLVE_MESSAGES = /** @type {Record<string, string>} */ ({
  "orders.issue_not_open": "Someone already resolved this issue. The queue is up to date now.",
  "orders.issue_not_found": "That issue no longer exists. Reload the page.",
  "orders.order_refunded": "This order was already refunded, so there is nothing left to credit.",
  "auth.forbidden": "This issue belongs to another hub.",
  "request.idempotency_mismatch": "That did not go through. Try again.",
});

/**
 * The message for a refusal that carries an amount: the largest credit that would be accepted
 * (`orders.issue_credit_invalid`, `max_paise`), or the room left in the wallet (`billing.wallet_limit`,
 * `room_paise`). Null for every other code.
 * @param {string} code
 * @param {Record<string, unknown>} extra
 */
export function amountRefusal(code, extra) {
  if (code === "orders.issue_credit_invalid") {
    const max = Number(extra.max_paise);
    return Number.isFinite(max)
      ? `That is more than can be credited. The most is ${formatRupees(max)}.`
      : "That is more than can be credited for this order.";
  }
  if (code === "billing.wallet_limit") {
    const room = Number(extra.room_paise);
    return Number.isFinite(room)
      ? `The customer's wallet has room for only ${formatRupees(room)} more. The issue stays open; credit less.`
      : "The customer's wallet is full. The issue stays open; credit less.";
  }
  return null;
}

/**
 * Checks the resolve form and builds the request body, or says what to fix. A reject needs a note;
 * a credit needs an amount above zero and at most `maxPaise`.
 * @param {{ outcome: "credit" | "reject", rupees: string, note: string, maxPaise: number }} input
 * @returns {{ body: { outcome: "credit" | "reject", credit_paise?: number, note?: string } } | { error: string, field: "rupees" | "note" }}
 */
export function resolveBody({ outcome, rupees, note, maxPaise }) {
  const text = note.trim();
  if (text.length > 500) return { field: "note", error: "Keep the note under 500 characters." };
  if (outcome === "reject") {
    if (!text) return { field: "note", error: "Say why, so the customer's app can show it." };
    return { body: { outcome, note: text } };
  }
  const paise = parseDecimal(rupees);
  if (paise === null || paise === 0)
    return { field: "rupees", error: "Enter an amount in rupees, like 40 or 40.50." };
  if (paise > maxPaise)
    return { field: "rupees", error: `The most you can credit is ${formatRupees(maxPaise)}.` };
  return { body: { outcome, credit_paise: paise, ...(text && { note: text }) } };
}

/**
 * Paise as the text of an amount field: 4000 → "40", 4050 → "40.50".
 * @param {number} paise
 */
export function rupeesText(paise) {
  const rupees = Math.floor(paise / 100);
  const rest = paise % 100;
  return rest === 0 ? String(rupees) : `${rupees}.${String(rest).padStart(2, "0")}`;
}
