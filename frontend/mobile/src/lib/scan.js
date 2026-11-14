import { MOCK } from "@/lib/env.js";
import { scanQr } from "@/shared/native/scanner.js";

/**
 * Reads a bag, crate or item code. In the Android app it opens Google's scanner (ML Kit, ADR-0034)
 * and returns the QR text as printed, trimmed; what the text means is for the screen (or
 * `parseScan`) to decide. The PWA on mocks "scans" the code the screen expects next, so every flow
 * can be walked through. On the web with the real API there is no scanner and it returns null, and
 * so it does when the person closes the scanner; every scan screen then offers typing the code.
 * Throws ScannerUnavailableError when the phone cannot scan (no Play services): offer typing.
 * @param {{ expected?: string, onPreparing?: (preparing: boolean) => void }} [options]
 *   `onPreparing(true)` while the scanner downloads on first use
 * @returns {Promise<string | null>}
 */
export async function scanCode({ expected, onPreparing } = {}) {
  if (MOCK) {
    if (!expected) return null;
    await new Promise((resolve) => setTimeout(resolve, 350));
    return expected;
  }
  const raw = await scanQr({ onPreparing });
  return raw?.trim() || null;
}

/** Bag and crate codes are printed in capitals with a dash ("B-214", "S-1"); people type them loosely. */
export const normalizeCode = (/** @type {string} */ raw) =>
  raw
    .trim()
    .toUpperCase()
    .replace(/\s+/g, "")
    .replace(/^([A-Z]+)(\d)/, "$1-$2");

/**
 * What a scanned or typed code is: a bag (`GB1:` + the order's 32 hex), a kitchen pickup (`GB1A:` +
 * the assignment's 32 hex) or a printed short code ("B-214", "S-1", "T-12"). Anything else is null.
 * The hex may come in capitals; `value` is canonical (capital prefix, lowercase hex, or the short
 * code in print form).
 * @param {string} raw
 * @returns {{ kind: "bag" | "pickup" | "short", value: string } | null}
 */
export function parseScan(raw) {
  const text = raw.trim();
  const qr = /^(GB1A?):([0-9a-fA-F]{32})$/.exec(text);
  if (qr) {
    const [, prefix, hex] = qr;
    return { kind: prefix === "GB1" ? "bag" : "pickup", value: `${prefix}:${hex?.toLowerCase()}` };
  }
  const short = normalizeCode(text);
  return /^[A-Z]{1,3}-\d{1,6}$/.test(short) ? { kind: "short", value: short } : null;
}
