// The QR printed on an order's bag (M6 §3.2), for the rider's return-leg bag pickups.

/** The QR printed on an order's bag: `GB1:` + the order id's 32 hex digits (M6 §3.2). @param {string} orderId */
export const bagQr = (orderId) => `GB1:${orderId.replaceAll("-", "").toLowerCase()}`;

/**
 * A bag's QR, scanned or typed: `GB1:` (capitals allowed) + 32 hex digits. Returns the canonical
 * form (capital prefix, lowercase hex) that `bagQr` makes, or null for anything else (a short code
 * such as "B-214" is not enough to name a bag to the server).
 * @param {string} raw
 */
export function parseBagQr(raw) {
  const m = /^GB1:([0-9a-fA-F]{32})$/.exec(raw.trim());
  return m ? `GB1:${m[1]?.toLowerCase()}` : null;
}
