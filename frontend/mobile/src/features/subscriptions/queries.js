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
 * @typedef {"draft" | "awaiting_approval" | "approved" | "skipped" | "cancelled" | "locked" | "payment_failed" | "preview"} CycleStatus
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
 * @property {{ quote_id: string, saving_paise: number, reason_code: string } | null} [green_slot] a slot move at a quoted saving (M3 §3.6)
 * @property {number} [discount_paise] the green-slot saving, else 0
 * @property {number} [fees_paise]
 * @property {number} [payable_paise] total_paise + fees_paise - discount_paise
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
 * A home kitchen's lunch or dinner tiffin the family can add to a day of the week: one approved
 * menu of GET /v1/tiffin-menus (M4 §3.5), under the names the Week screens use.
 * @typedef {{ menu_id: string, date: string, slot: "lunch" | "dinner", kitchen_id: string, cook: { name: string }, dish: { name: string, items?: string }, price_paise: number, order_by: string }} TiffinOffer
 * @typedef {{ id: string, date: string, meal: "lunch" | "dinner", hub_id: string, dishes: { recipe_id: string, name: string }[], price_paise: number, kitchen: { partner_id: string, display_name: string, photo_url: string | null }, order_by: string }} TiffinMenu
 */

/**
 * A menu as an offer. The menu has no thali name or item list, only its dishes, so `dish.name` is
 * the dishes joined and `dish.items` is left out; the cook is the menu's kitchen (a preference: the
 * fair assignment may give the order to another kitchen), and `kitchen_id` is her partner id.
 * @param {TiffinMenu} m @returns {TiffinOffer}
 */
export const menuToOffer = (m) => ({
  menu_id: m.id,
  date: m.date,
  slot: m.meal,
  kitchen_id: m.kitchen.partner_id,
  cook: { name: m.kitchen.display_name },
  dish: { name: m.dishes.map((d) => d.name).join(", ") },
  price_paise: m.price_paise,
  order_by: m.order_by,
});

/**
 * Tiffins on offer for the days of a week, while it is still open (GET /v1/tiffin-menus: the
 * approved menus of the caller's hub from tomorrow for a week, still open to book). The cycle does
 * not carry offers. The read has no range, so the menus are cut to the days of this week; lunch
 * comes before dinner on a day, which is the one the day sheet shows.
 * @param {Cycle | null | undefined} cycle
 */
export function useTiffinOffers(cycle) {
  const days = cycle?.meal_plan.days ?? [];
  const from = days[0]?.date;
  const to = days.at(-1)?.date;
  return useQuery({
    queryKey: ["tiffin-menus", from, to],
    queryFn: () => /** @type {Promise<{ items: TiffinMenu[] }>} */ (request("/v1/tiffin-menus")),
    enabled: isEditable(cycle) && !!from,
    retry: false,
    select: (r) =>
      r.items
        .filter((m) => m.date >= String(from) && m.date <= String(to))
        .map(menuToOffer)
        .sort(
          (a, b) =>
            a.date.localeCompare(b.date) || (a.slot === b.slot ? 0 : a.slot === "lunch" ? -1 : 1),
        ),
  });
}

/**
 * Approve or skip the week (POST /v1/cycles/{id}/approve|skip, M3 PR #102, not shipped yet);
 * approve also sends If-Match with the version the person saw (skip takes only the key). The
 * Idempotency-Key belongs to the intent, not the tap: approving version 3 of this week always
 * sends the same key, so a retry after a lost response replays instead of acting twice.
 * `unskip` is our proposal (the spec has no way back from a skip), kept for the Skipped design.
 * @param {"approve" | "skip" | "unskip"} action
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
            ...(action === "skip" ? {} : { "if-match": ifMatch(cycle) }),
          },
        })
      ),
    onSuccess: () => client.invalidateQueries({ queryKey: ["cycle"] }),
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

/**
 * One edit of the box, applied in order with the others in a request (M3 §3.1). Quantities are in
 * steps, 1 to 20; `add` always carries one.
 * @typedef {{ op: "remove", product_id: string } | { op: "add", product_id: string, qty: number } | { op: "qty", product_id: string, qty: number } | { op: "substitute", product_id: string, substitute_id: string }} LineOp
 */

/** The ops that put a swapped line back: take the replacement out and add what it replaced. @param {BoxLine} line @returns {LineOp[]} */
export const undoSwapOps = (line) =>
  line.replaces
    ? [
        { op: "remove", product_id: line.product_id },
        { op: "add", product_id: line.replaces.product_id, qty: line.qty },
      ]
    : [];

/**
 * Swap, resize, remove or add box lines (PATCH /v1/cycles/{id}/lines with { ops } and If-Match,
 * M3 PR #102, not shipped yet; 1 to 20 ops). Resolves to the cycle at its new version.
 */
export function useEditCycle() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (/** @type {{ cycle: Cycle, ops: LineOp[] }} */ v) =>
      /** @type {Promise<Cycle>} */ (
        request(`/v1/cycles/${v.cycle.id}/lines`, {
          method: "PATCH",
          headers: { "if-match": ifMatch(v.cycle) },
          body: { ops: v.ops },
        })
      ),
    onSuccess: () => client.invalidateQueries({ queryKey: ["cycle"] }),
  });
}

/** Swap one planned meal for another (PUT /v1/cycles/{id}/meals, M3), with If-Match; `set` defaults to "next". */
export function useSwapMeal() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (/** @type {{ cycle: Cycle, date: string, slot: PlannedMeal["slot"] }} */ v) =>
      /** @type {Promise<Cycle>} */ (
        request(`/v1/cycles/${v.cycle.id}/meals`, {
          method: "PUT",
          body: { date: v.date, slot: v.slot },
          headers: { "if-match": ifMatch(v.cycle) },
        })
      ),
    onSuccess: () => client.invalidateQueries({ queryKey: ["cycle", "current"] }),
  });
}

/**
 * A box order whose payment didn't go through at the cut-off (OrderSummaryOut, M4). It stays
 * `payment_failed` until the debit is retried or the grace period ends.
 * @typedef {{ id: string, kind: "box" | "tiffin" | "addon", status: string, cycle_id: string | null, delivery_date: string, cutoff_at: string, total_paise: number, version: number }} BoxOrder
 */

/**
 * Whether this cycle's box order can be looked up: approved or locked, or `payment_failed` when
 * the wallet was short at the cut-off (the cycle then carries that status itself).
 * @param {Pick<Cycle, "status">} cycle
 */
export const hasBoxOrder = (cycle) =>
  cycle.status === "approved" || cycle.status === "locked" || cycle.status === "payment_failed";

/** Two hours after the cut-off, the grace period before an unpaid box is cancelled (M4 §3.1, 23:00 IST). @param {Pick<BoxOrder, "cutoff_at">} order */
export const graceEnds = (order) =>
  new Date(Date.parse(order.cutoff_at) + 2 * 3_600_000).toISOString();

/**
 * This cycle's box order while it sits in `payment_failed`, or null (GET /v1/orders?status=payment_failed,
 * M4). The week shows the "payment didn't go through" state from it, with the wallet. With `poll`,
 * it re-reads every 2 s: a top-up in the grace period re-runs the debit (PR #103), so the order
 * leaves this list once it is paid. Only a cycle that has an order can have a failed one.
 * @param {Cycle | null | undefined} cycle
 * @param {{ poll?: boolean }} [options]
 */
export function useFailedBoxOrder(cycle, { poll = false } = {}) {
  return useQuery({
    queryKey: ["orders", "payment-failed", cycle?.id],
    queryFn: async () => {
      const page = await request("/v1/orders?status=payment_failed");
      return (
        /** @type {{ items: BoxOrder[] }} */ (page).items.find(
          (o) => o.kind === "box" && o.cycle_id === cycle?.id,
        ) ?? null
      );
    },
    enabled: !!cycle?.order_id && hasBoxOrder(cycle),
    refetchInterval: poll ? 2000 : false,
  });
}

/**
 * Let a box that can't be paid go, with nothing charged (POST /v1/orders/{id}/cancel, M4).
 * @proposal (not in the contract yet) M4 documents cancel only "while approved"; the Payment-failed
 * design's "Skip this week instead" needs it to also work while the order is `payment_failed`.
 */
export function useCancelBox() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (/** @type {BoxOrder} */ order) =>
      request(`/v1/orders/${order.id}/cancel`, {
        method: "POST",
        body: {},
        headers: { "idempotency-key": crypto.randomUUID() },
      }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["orders"] });
      client.invalidateQueries({ queryKey: ["cycle"] });
    },
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

/** Set or turn off the monthly limit (PUT /v1/budget-lock, M3). Retrying the same change reuses its Idempotency-Key. */
export function useSaveBudgetLock() {
  const client = useQueryClient();
  const keys = useRef(/** @type {Record<string, string>} */ ({}));
  return useMutation({
    mutationFn: async (/** @type {{ budget_paise: number | null }} */ { budget_paise }) => {
      const body = { month: istDate().slice(0, 7), budget_paise };
      const id = JSON.stringify(body);
      keys.current[id] ??= crypto.randomUUID();
      return /** @type {BudgetLock} */ (
        await request("/v1/budget-lock", {
          method: "PUT",
          body,
          headers: { "idempotency-key": keys.current[id] },
        })
      );
    },
    onSuccess: () => {
      keys.current = {};
      client.invalidateQueries({ queryKey: ["budget-lock"] });
      client.invalidateQueries({ queryKey: ["cycle"] });
      client.invalidateQueries({ queryKey: ["household", "me"] });
    },
  });
}
