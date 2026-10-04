import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { request } from "@/lib/api.js";

/**
 * @typedef {{ slot: "lunch" | "dinner", name: string, tiffin: boolean, skip?: boolean }} PlannedMeal
 * @typedef {{ product_id: string, name: string, qty_label: string, price_paise: number, reason_code: string | null, reason_params: Record<string, string | number> | null, swapped_from_paise: number | null }} BoxLine
 * @typedef {{ code: string, params: Record<string, string | number> }} Highlight
 * @typedef {object} Cycle
 * @property {string} id
 * @property {string} week_start ISO date
 * @property {string} delivery_date ISO date
 * @property {{ from: string, to: string }} delivery_window
 * @property {string} cutoff_at
 * @property {"ready" | "approved" | "skipped" | "locked"} status
 * @property {number} version
 * @property {{ days: { date: string, meals: PlannedMeal[] }[] }} meal_plan
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
 * Approve or skip this week (POST /v1/cycles/{id}/approve|skip), with If-Match on the version the
 * person saw. The Idempotency-Key belongs to the intent, not the tap: approving version 3 of this
 * week always sends the same key, so a retry after a lost response replays instead of acting twice.
 * @param {"approve" | "skip"} action
 */
export function useCycleAction(action) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (/** @type {Cycle} */ cycle) =>
      /** @type {Promise<Cycle>} */ (
        request(`/v1/cycles/${cycle.id}/${action}`, {
          method: "POST",
          body: {},
          headers: {
            "idempotency-key": `cycle:${cycle.id}:${action}:v${cycle.version}`,
            "if-match": String(cycle.version),
          },
        })
      ),
    onSuccess: () => client.invalidateQueries({ queryKey: ["cycle", "current"] }),
  });
}
