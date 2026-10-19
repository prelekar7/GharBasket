/**
 * Kindness points of a donation (M9 section 3.4), the rule the hub's verify screen previews and the server
 * decides:
 *   points = round_half_up(sum of counted x rate.points x (the drive's multiplier for that line, else 1))
 * Each line shows its own rounded share and the largest line takes the rounding remainder, so the lines add
 * up to the total. Quantities are whole pieces or kilograms in halves; a multiplier has one decimal, so all
 * of it is integer arithmetic (halves x tenths), never a float.
 *
 * @typedef {{ code: string, label: string, unit: "kg" | "each", points: number, max_qty: number }} PointRate
 * @typedef {{ multiplier: number, codes: string[] } | null} DriveRule the drive a donation was scheduled in
 */

/**
 * @param {Record<string, number>} counted code → counted quantity in the rate's unit
 * @param {PointRate[]} rates
 * @param {DriveRule} drive
 * @returns {{ total: number, lines: Record<string, number> }}
 */
export function donationPoints(counted, rates, drive) {
  const mult10 = drive ? Math.round(drive.multiplier * 10) : 10;
  const parts = Object.entries(counted).map(([code, qty]) => {
    const rate = rates.find((r) => r.code === code)?.points ?? 0;
    const q2 = Math.round((Number(qty) || 0) * 2);
    const m = drive?.codes.includes(code) ? mult10 : 10;
    return { code, num: q2 * rate * m };
  });
  const half = (/** @type {number} */ num) => Math.floor((num + 10) / 20);
  const total = half(parts.reduce((n, p) => n + p.num, 0));
  /** @type {Record<string, number>} */
  const lines = Object.fromEntries(parts.map((p) => [p.code, half(p.num)]));
  const rest = total - Object.values(lines).reduce((n, v) => n + v, 0);
  if (rest !== 0 && parts.length > 0) {
    const biggest = parts.reduce((a, b) => (b.num > a.num ? b : a));
    lines[biggest.code] += rest;
  }
  return { total, lines };
}

/**
 * Why a counted quantity is not allowed, or null: whole pieces or halves of a kilogram, 0 or more, and at
 * most twice the rate's `max_qty`.
 * @param {PointRate} rate
 * @param {number} qty
 * @returns {string | null}
 */
export function countProblem(rate, qty) {
  if (!Number.isFinite(qty) || qty < 0) return "0 or more";
  if (rate.unit === "each" && !Number.isInteger(qty)) return "Whole pieces";
  if (rate.unit === "kg" && !Number.isInteger(qty * 2)) return "In steps of 0.5 kg";
  if (qty > rate.max_qty * 2) return `At most ${rate.max_qty * 2}`;
  return null;
}
