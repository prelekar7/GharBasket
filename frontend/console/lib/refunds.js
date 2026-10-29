// Words for the Refunds page (`GET /v1/admin/refunds`, M5 §3.8): the API gives codes, the console words them.

/** @typedef {"shortage" | "issue" | "undelivered" | "no_kitchen" | "carry_over" | "other"} RefundReason */
/** @typedef {"credited" | "pending" | "processing" | "processed" | "partly_failed" | "failed"} RefundStatus */

/** @type {Record<RefundReason, string>} */
const REASONS = {
  shortage: "Short item",
  issue: "Issue credit",
  undelivered: "Not delivered",
  no_kitchen: "No kitchen could take it",
  carry_over: "Carry-over",
  other: "Other",
};

/** @type {Record<RefundStatus, string>} */
const STATUSES = {
  credited: "In the wallet",
  pending: "Waiting to go to the bank",
  processing: "On its way to the bank",
  processed: "Back in the bank",
  partly_failed: "Partly back in the bank",
  failed: "Failed to reach the bank",
};

/** @type {Record<string, string>} */
const KINDS = { box: "Box", tiffin: "Tiffin", addon: "Add-on" };

/** @param {string} reason */
export const reasonText = (reason) => REASONS[/** @type {RefundReason} */ (reason)] ?? "Other";

/** @param {string} status */
export const statusText = (status) => STATUSES[/** @type {RefundStatus} */ (status)] ?? status;

/** @param {string} kind */
export const kindText = (kind) => KINDS[kind] ?? kind;

/** A refund that did not fully reach the bank is the only kind someone may need to look at. @param {string} status */
export const needsAttention = (status) => status === "failed" || status === "partly_failed";
