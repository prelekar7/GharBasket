// Stock the hub holds, for the M6 mocks (§3.4): inventory lots with a best-before, graded A, B or C.
// Receiving adds lots, waste and the surplus cascade take them away, and the procurement plan nets the
// usable ones (grade A and B whose best-before is after the end of the delivery day, IST).
import { mockStore, mockUuid } from "./util.js";

/**
 * @typedef {{ id: string, hub_id: string, product_id: string, source: "receipt" | "returned",
 *   grade: "A" | "B" | "C", qty_in: number, qty_on_hand: number, unit_cost_paise: number | null,
 *   received_at: string, best_before: string, receipt_id: string | null, vendor_id: string | null }} Lot
 */

/** @type {() => { lots: Lot[] }} */
export const inventoryState = () => mockStore("inventory", () => ({ lots: [] }));

/**
 * Puts a new lot on the shelf (`qty_in` = `qty_on_hand`).
 * @param {Omit<Lot, "id" | "qty_on_hand">} lot
 * @returns {Lot}
 */
export function addLot(lot) {
  const { lots } = inventoryState();
  /** @type {Lot} */
  const added = { ...lot, id: mockUuid("c140", lots.length + 1), qty_on_hand: lot.qty_in };
  lots.push(added);
  return added;
}

/**
 * Usable stock of a product for a delivery date D (§3.4): Σ qty_on_hand of grade A and B lots whose
 * best-before is after the end of D (IST), so what is packed on D lasts the day.
 * @param {string} productId @param {string} date `YYYY-MM-DD`
 */
export function usableStock(productId, date) {
  const endOfDay = Date.parse(`${date}T23:59:59.999+05:30`);
  const sum = inventoryState()
    .lots.filter(
      (l) => l.product_id === productId && l.grade !== "C" && Date.parse(l.best_before) > endOfDay,
    )
    .reduce((n, l) => n + l.qty_on_hand, 0);
  return Math.round(sum * 1000) / 1000;
}
