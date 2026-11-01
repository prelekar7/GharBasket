import { MOCK } from "@/lib/env.js";

/**
 * Reads a bag, crate or item code. The camera scanner arrives with the Capacitor build (ML Kit,
 * Phase 4); until then the PWA on mocks "scans" the code the screen expects next, so every flow can
 * be walked through. With the real API and no scanner it returns null and the screen falls back to
 * typing the code, which every scan screen offers.
 * @param {{ expected?: string }} [options]
 * @returns {Promise<string | null>}
 */
export async function scanCode({ expected } = {}) {
  if (!MOCK || !expected) return null;
  await new Promise((resolve) => setTimeout(resolve, 350));
  return expected;
}

/** Bag and crate codes are printed in capitals with a dash ("B-214", "S-1"); people type them loosely. */
export const normalizeCode = (/** @type {string} */ raw) =>
  raw
    .trim()
    .toUpperCase()
    .replace(/\s+/g, "")
    .replace(/^([A-Z]+)(\d)/, "$1-$2");

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
