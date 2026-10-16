/**
 * Operations units of M6 (ADR-0026 §1): stock, plans, receipts and waste count a product in `kg`, `l`,
 * `piece`, `bunch` or `pack`, whatever step the customer buys it in. No imports, so client and server
 * code can both use it.
 */

/** Products M6 buys for stock (§3.3); tiffins are cooked by kitchens and kits are not built yet. */
export const STOCKED_KINDS = ["veg", "fruit", "staple", "salad"];

/** @typedef {"kg" | "l" | "piece" | "bunch" | "pack"} OpsUnit */

/**
 * The operations unit of a product from its catalog step: grams roll up to kg, millilitres to l.
 * @param {{ unit: string }} step
 * @returns {OpsUnit}
 */
export function opsUnit(step) {
  if (step.unit === "g" || step.unit === "kg") return "kg";
  if (step.unit === "ml" || step.unit === "l") return "l";
  return step.unit === "bunch" || step.unit === "pack" ? step.unit : "piece";
}

/** Pieces, bunches and packs are counted whole; kg and l may have up to three decimals. @param {string} unit */
export const isWholeUnit = (unit) => unit === "piece" || unit === "bunch" || unit === "pack";

/** @type {Record<OpsUnit, string>} */
const PLURAL = { kg: "kg", l: "l", piece: "pieces", bunch: "bunches", pack: "packs" };

/**
 * "2.5 kg", "1 bunch", "6 bunches".
 * @param {number} n @param {string} unit
 */
export function withUnit(n, unit) {
  return `${n} ${n === 1 ? unit : (PLURAL[/** @type {OpsUnit} */ (unit)] ?? unit)}`;
}

/**
 * Parses a typed quantity for an operations unit: a number from `min`, at most three decimals, whole
 * for pieces, bunches and packs. `null` when it is not valid.
 * @param {string} text @param {string} unit @param {number} [min]
 * @returns {number | null}
 */
export function parseQty(text, unit, min = 0) {
  const t = text.trim();
  if (!/^\d+(\.\d{1,3})?$/.test(t)) return null;
  const n = Number(t);
  if (n < min || n > 9_999_999.999) return null;
  return isWholeUnit(unit) && !Number.isInteger(n) ? null : n;
}
