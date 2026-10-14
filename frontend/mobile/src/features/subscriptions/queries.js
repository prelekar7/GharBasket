import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { request } from "@/lib/api.js";

/**
 * @typedef {{ slot: "lunch" | "dinner", name: string, tiffin: boolean, skip?: boolean, reason_code?: string | null, reason_params?: Record<string, string | number> | null }} PlannedMeal
 * @typedef {{ kitchen_id: string, slot: "lunch" | "dinner", cook: { name: string }, dish: { name: string }, price_paise: number, order_by: string }} TiffinOffer
 * @typedef {{ product_id: string, name: string, qty_label: string, qty?: number, step?: { label: string, price_paise: number }, used_in?: { date: string, slot: "lunch" | "dinner", dish: string }[], price_paise: number, reason_code: string | null, reason_params: Record<string, string | number> | null, swapped_from_paise: number | null }} BoxLine
 * @typedef {{ code: string, params: Record<string, string | number> }} Highlight
 * @typedef {object} Cycle
 * @property {string} id
 * @property {string} week_start ISO date
 * @property {string} delivery_date ISO date
 * @property {{ from: string, to: string }} delivery_window
 * @property {string} cutoff_at
 * @property {"ready" | "approved" | "skipped" | "locked" | "payment_failed"} status
 * @property {number} version
 * @property {string | null} approved_at
 * @property {{ name: string }} hub
 * @property {{ wallet_paise: number, shortfall_paise: number, due_at: string } | null} payment_due
 * @property {{ id: string, product_id: string, item: string, substitute: string, pct: number, market: string, meals: number, keep_delta_paise: number }[]} [price_alerts]
 * @property {{ days: { date: string, meals: PlannedMeal[], tiffin_offer?: TiffinOffer | null }[] }} meal_plan
 * @property {BoxLine[]} lines
 * @property {number} total_paise
 * @property {Highlight[]} highlights
 * @property {{ budget_paise: number, spent_paise: number } | null} budget_lock
 * @property {{ name: string, flat: string } | null} captain
 */

/** This week's cycle: meal plan, box lines and status (GET /v1/cycles/current, M3). @param {string} lang */
export function useCurrentCycle(lang) {
  return useQuery({
    queryKey: ["cycle", "current", lang],
    queryFn: () => /** @type {Promise<Cycle>} */ (request("/v1/cycles/current")),
  });
}

/**
 * Approve, skip, undo a skip, or pay after a short wallet (POST /v1/cycles/{id}/approve|skip|unskip|pay).
 * Each attempt has its own Idempotency-Key and sends If-Match with the version the person saw.
 * @param {"approve" | "skip" | "unskip" | "pay"} action
 */
export function useCycleAction(action) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (/** @type {Cycle} */ cycle) =>
      /** @type {Promise<Cycle>} */ (
        request(`/v1/cycles/${cycle.id}/${action}`, {
          method: "POST",
          body: {},
          headers: { "idempotency-key": crypto.randomUUID(), "if-match": String(cycle.version) },
        })
      ),
    onSuccess: () => client.invalidateQueries({ queryKey: ["cycle", "current"] }),
  });
}

/** @typedef {{ product_id: string, name: string, qty_label: string, price_paise: number, price_delta_paise: number, reason_code: string | null, reason_params: Record<string, string | number> | null }} Substitute */

/** Three ranked substitutes for a box line (GET /v1/products/{id}/substitutes, M2). @param {string | null} productId @param {string} lang */
export function useSubstitutes(productId, lang) {
  return useQuery({
    queryKey: ["substitutes", productId, lang],
    queryFn: () =>
      /** @type {Promise<{ items: Substitute[] }>} */ (
        request(`/v1/products/${productId}/substitutes`)
      ),
    enabled: !!productId,
  });
}

/** Swap a box line for a substitute (PATCH /v1/cycles/{id}/lines with If-Match). */
export function useSwapLine() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (/** @type {{ cycle: Cycle, productId: string, substituteId: string }} */ v) =>
      /** @type {Promise<Cycle>} */ (
        request(`/v1/cycles/${v.cycle.id}/lines`, {
          method: "PATCH",
          headers: { "if-match": String(v.cycle.version) },
          body: { op: "substitute", product_id: v.productId, substitute_id: v.substituteId },
        })
      ),
    onSuccess: () => client.invalidateQueries({ queryKey: ["cycle", "current"] }),
  });
}

/** Swap one planned meal for another (PATCH /v1/cycles/{id}/meals, our proposal for M3), with If-Match. */
export function useSwapMeal() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (/** @type {{ cycle: Cycle, date: string, slot: "lunch" | "dinner" }} */ v) =>
      /** @type {Promise<Cycle>} */ (
        request(`/v1/cycles/${v.cycle.id}/meals`, {
          method: "PATCH",
          body: { date: v.date, slot: v.slot },
          headers: { "if-match": String(v.cycle.version) },
        })
      ),
    onSuccess: () => client.invalidateQueries({ queryKey: ["cycle", "current"] }),
  });
}

/**
 * Change how much of a line comes, take it out, put it back, or add a catalog item (PATCH
 * /v1/cycles/{id}/lines with op qty | remove | restore | add, our proposal for M3), with If-Match.
 */
export function useEditLine() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (
      /** @type {{ cycle: Cycle, productId: string, op: "qty" | "remove" | "restore" | "add" | "undo_swap" | "dismiss_alert", qty?: number }} */ v,
    ) =>
      /** @type {Promise<Cycle>} */ (
        request(`/v1/cycles/${v.cycle.id}/lines`, {
          method: "PATCH",
          body: { op: v.op, product_id: v.productId, qty: v.qty },
          headers: { "if-match": String(v.cycle.version) },
        })
      ),
    onSuccess: () => client.invalidateQueries({ queryKey: ["cycle", "current"] }),
  });
}
