import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef } from "react";
import { ApiError, request } from "@/lib/api.js";
import { istDate } from "./format.js";

/**
 * The real CycleOut (M3 §3.1, contract/openapi.json). Money is integer paise; times are UTC with
 * "Z" (show them in India time); ids are UUIDs and `slug` carries the readable name.
 * @typedef {{ qty: number, unit: "g" | "kg" | "ml" | "l" | "piece" | "bunch" | "pack", label: string }} Step one step is what a person adds or removes; prices are per step
 * @typedef {{ product_id: string, slug: string, name: string, unit_price_paise: number }} Replaced
 * @typedef {{ product_id: string, slug: string, name: string, qty: number, step: Step, unit_price_paise: number, total_paise: number, reason_code: string, reason_params: Record<string, any> | null, source: "ai" | "rule" | "customer", replaces: Replaced | null }} BoxLine qty is in steps
 * @typedef {{ slot: "breakfast" | "lunch" | "dinner", recipe_id: string | null, name: string | null, tiffin: boolean, sides: string[] }} PlannedMeal no recipe and tiffin: a no-cook slot
 * @typedef {{ code: string, params: Record<string, any> }} Highlight
 * @typedef {{ id: string, label: string, start: string, end: string }} Slot
 * @typedef {"draft" | "awaiting_approval" | "approved" | "skipped" | "cancelled" | "locked" | "preview"} CycleStatus
 * @typedef {object} Cycle
 * @property {string | null} id null for a preview
 * @property {string} household_id
 * @property {string} week_start Monday, ISO date
 * @property {string} delivery_date ISO date
 * @property {Slot} slot
 * @property {string} cutoff_at UTC; 21:00 IST the day before delivery
 * @property {CycleStatus} status
 * @property {number} version
 * @property {{ source: "ml" | "fallback", days: { date: string, meals: PlannedMeal[] }[] }} meal_plan
 * @property {BoxLine[]} lines
 * @property {number} total_paise
 * @property {Highlight[]} highlights
 * @property {{ month: string, budget_paise: number, spent_paise: number, remaining_paise: number } | null} budget_lock
 * @property {{ name: string, flat: string } | null} captain null until M9 captains
 * @property {string | null} order_id after approval
 * @property {string | null} generated_at
 * @property {string | null} approved_at
 */

/** Edits and approval stay open from "check it" until the cut-off, even after approving (M3 §3.1). */
export const isEditable = (/** @type {Pick<Cycle, "status"> | null | undefined} */ c) =>
  c?.status === "awaiting_approval" || c?.status === "approved";

/** The If-Match for a cycle: its version as the quoted ETag the API sends. @param {Pick<Cycle, "version">} c */
export const ifMatch = (c) => `"${c.version}"`;

/**
 * This week's cycle: meal plan, box lines and status (GET /v1/cycles/current, M3). Before the first
 * generation, and while paused, the API answers 404 `subscriptions.no_cycle`: that is an empty
 * state, so it resolves to null instead of failing. @param {string} lang
 */
export function useCurrentCycle(lang) {
  return useQuery({
    queryKey: ["cycle", "current", lang],
    queryFn: async () => {
      try {
        return /** @type {Cycle} */ (await request("/v1/cycles/current"));
      } catch (e) {
        if (e instanceof ApiError && e.code === "subscriptions.no_cycle") return null;
        throw e;
      }
    },
  });
}

/**
 * The first week for onboarding, from today's prices and the household's rules; nothing is saved
 * (GET /v1/cycles/preview, M3). 422 `subscriptions.no_delivery_day` means the society has no
 * delivery day yet; the screen shows that as its own state. @param {string} lang
 */
export function usePreviewCycle(lang) {
  return useQuery({
    queryKey: ["cycle", "preview", lang],
    queryFn: () => /** @type {Promise<Cycle>} */ (request("/v1/cycles/preview")),
    retry: false,
  });
}

/**
 * A home kitchen's lunch or dinner tiffin the family can add to a day of the week.
 * @typedef {{ date: string, slot: "lunch" | "dinner", kitchen_id: string, cook: { name: string }, dish: { name: string, items?: string }, price_paise: number, order_by: string }} TiffinOffer
 */

/**
 * Tiffins on offer for the days of a week, while it is still open.
 * @proposal (not in the contract yet) GET /v1/tiffin-offers?from=YYYY-MM-DD&to=YYYY-MM-DD → { items: TiffinOffer[] }.
 * M3 plans the week and M8 kitchens cook; the designs (Core-W2, HcTiffin) offer a tiffin inside a
 * day, so this needs one read that joins them. Until it exists the offer is simply not shown.
 * @param {Cycle | null | undefined} cycle
 */
export function useTiffinOffers(cycle) {
  const days = cycle?.meal_plan.days ?? [];
  const from = days[0]?.date;
  const to = days.at(-1)?.date;
  return useQuery({
    queryKey: ["tiffin-offers", from, to],
    queryFn: () =>
      /** @type {Promise<{ items: TiffinOffer[] }>} */ (
        request(`/v1/tiffin-offers?from=${from}&to=${to}`)
      ),
    enabled: isEditable(cycle) && !!from,
    retry: false,
    select: (r) => r.items,
  });
}

/**
 * Approve or skip the week (POST /v1/cycles/{id}/approve|skip, M3). Each attempt has its own
 * Idempotency-Key and sends If-Match with the version the person saw. `unskip` and `pay` are our
 * proposals (the spec has neither), kept for the Skipped and Payment-failed designs.
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
          headers: { "idempotency-key": crypto.randomUUID(), "if-match": ifMatch(cycle) },
        })
      ),
    onSuccess: () => client.invalidateQueries({ queryKey: ["cycle", "current"] }),
  });
}

/**
 * @typedef {{ product_id: string, slug: string, name: string, step: Step, price_paise: number | null, price_delta_paise: number | null, score: number, reason_code: "similar_use" | "same_family" | "cheaper" | "in_season", reason_params: Record<string, number | string> }} Substitute
 * price_paise is for one step, and null when the hub has no price
 */

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
          headers: { "if-match": ifMatch(v.cycle) },
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
    mutationFn: (/** @type {{ cycle: Cycle, date: string, slot: PlannedMeal["slot"] }} */ v) =>
      /** @type {Promise<Cycle>} */ (
        request(`/v1/cycles/${v.cycle.id}/meals`, {
          method: "PATCH",
          body: { date: v.date, slot: v.slot },
          headers: { "if-match": ifMatch(v.cycle) },
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
          headers: { "if-match": ifMatch(v.cycle) },
        })
      ),
    onSuccess: () => client.invalidateQueries({ queryKey: ["cycle", "current"] }),
  });
}

/**
 * @typedef {{ month: string, budget_paise: number | null, spent_paise: number, remaining_paise: number }} BudgetLock
 * budget_paise is null while Budget Lock is off: the API answers 404 `subscriptions.no_budget_lock`
 * for a month with none, and the screens read that as "off" (spent is then unknown, shown as 0).
 */

/** This month's Budget Lock: limit, spent so far, left (GET /v1/budget-lock?month=, M3). */
export function useBudgetLock() {
  const month = istDate().slice(0, 7);
  return useQuery({
    queryKey: ["budget-lock", month],
    queryFn: async () => {
      try {
        return /** @type {BudgetLock} */ (await request(`/v1/budget-lock?month=${month}`));
      } catch (e) {
        if (e instanceof ApiError && e.code === "subscriptions.no_budget_lock")
          return /** @type {BudgetLock} */ ({
            month,
            budget_paise: null,
            spent_paise: 0,
            remaining_paise: 0,
          });
        throw e;
      }
    },
  });
}

/**
 * @typedef {"swap" | "trim" | "ask"} WhenOver what Autopilot does when a week would go over
 */

/**
 * What Autopilot does when a week would go over the limit (design HcBudget).
 * @proposal (not in the contract yet) GET and PUT /v1/budget-lock/preferences ↔ { when_over: "swap" | "trim" | "ask" }.
 * M3 only describes the "swap to cheaper" behaviour; the designs let a family pick two more. While
 * the endpoint is missing the question is not shown and Budget Lock works as the spec says.
 */
export function useBudgetPreferences() {
  return useQuery({
    queryKey: ["budget-lock", "preferences"],
    queryFn: () =>
      /** @type {Promise<{ when_over: WhenOver }>} */ (request("/v1/budget-lock/preferences")),
    retry: false,
  });
}

/**
 * Set or turn off the monthly limit (PUT /v1/budget-lock, M3) and, when given, what happens when a
 * week would go over (the proposed preferences). Retrying the same change reuses its Idempotency-Key.
 */
export function useSaveBudgetLock() {
  const client = useQueryClient();
  const keys = useRef(/** @type {Record<string, string>} */ ({}));
  return useMutation({
    mutationFn: async (
      /** @type {{ budget_paise: number | null, when_over?: WhenOver }} */ {
        budget_paise,
        when_over,
      },
    ) => {
      const body = { month: istDate().slice(0, 7), budget_paise };
      const id = JSON.stringify(body);
      keys.current[id] ??= crypto.randomUUID();
      const saved = await request("/v1/budget-lock", {
        method: "PUT",
        body,
        headers: { "idempotency-key": keys.current[id] },
      });
      if (when_over)
        await request("/v1/budget-lock/preferences", { method: "PUT", body: { when_over } });
      return /** @type {BudgetLock} */ (saved);
    },
    onSuccess: () => {
      keys.current = {};
      client.invalidateQueries({ queryKey: ["budget-lock"] });
      client.invalidateQueries({ queryKey: ["cycle"] });
      client.invalidateQueries({ queryKey: ["household", "me"] });
    },
  });
}
