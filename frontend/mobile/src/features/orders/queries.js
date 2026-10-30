import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef } from "react";
import { useSociety } from "@/features/community/queries.js";
import { useMyHousehold } from "@/features/identity/profile.js";
import { usePublicPartner } from "@/features/partners/queries.js";
import { ApiError, request } from "@/lib/api.js";
import { MOVING } from "./status.js";

/**
 * M4's order, as the API sends it (docs/modules/M4-orders.md §3.1). Amounts are paise, times are
 * UTC, `delivery_date` and slot times are India's. The list sends OrderSummary (no lines, no
 * timeline); one order sends both.
 * @typedef {"approved" | "locked" | "payment_failed" | "cancelled" | "picking" | "packed" | "dispatched" | "at_society" | "delivered" | "undelivered" | "redelivery" | "refunded"} OrderStatus
 * @typedef {{ id: string, label: string, start: string, end: string }} SlotRef
 * @typedef {{ id: string, kind: "box" | "tiffin" | "addon", status: OrderStatus, cycle_id: string | null,
 *   household_id: string, hub_id: string, society_id: string, delivery_date: string, slot: SlotRef,
 *   cutoff_at: string, subtotal_paise: number, fees_paise: number, discount_paise: number,
 *   total_paise: number, version: number, created_at: string, locked_at: string | null }} OrderSummary
 * @typedef {{ product_id: string, slug: string, name: string }} ProductRef
 * @typedef {{ product_id: string, slug: string, name: string, kind: "box" | "addon", qty: number,
 *   step: { qty: number, unit: string, label: string }, unit_price_paise: number, total_paise: number,
 *   reason_code: string | null, reason_params: Record<string, unknown> | null,
 *   substituted_from: ProductRef | null, shortage_qty: number }} OrderLine
 * @typedef {{ status: OrderStatus, at: string, actor: "customer" | "system" | "hub" }} TimelineEntry
 * @typedef {{ menu_id: string, meal: "lunch" | "dinner", dishes: { recipe_id: string, name: string }[] }} TiffinRef
 * @typedef {OrderSummary & { lines: OrderLine[], timeline: TimelineEntry[], tiffin?: TiffinRef | null,
 *   kitchen?: { partner_id: string } | null }} Order
 */

/** Polling stands in for M7's SSE stream until it exists; the real one pushes every 15 s. */
const LIVE_MS = 5000;

/**
 * The household's orders, newest delivery first (GET /v1/orders, M4). Boxes come from approved
 * weekly cycles, so a new household's list is empty. @param {string} lang
 */
export function useOrders(lang) {
  return useQuery({
    queryKey: ["orders", lang],
    queryFn: async () =>
      /** @type {{ items: OrderSummary[], next_cursor: string | null }} */ (
        await request("/v1/orders")
      ).items,
    refetchInterval: (q) => (q.state.data?.some((o) => MOVING.has(o.status)) ? LIVE_MS : false),
  });
}

/**
 * One order with its lines and timeline, kept fresh while it is on the way (GET /v1/orders/{id},
 * M4). @param {string | undefined} id @param {string} lang
 */
export function useOrder(id, lang) {
  return useQuery({
    queryKey: ["orders", id, lang],
    queryFn: () => /** @type {Promise<Order>} */ (request(`/v1/orders/${id}`)),
    enabled: !!id,
    refetchInterval: (q) => (MOVING.has(q.state.data?.status ?? "") ? LIVE_MS : false),
  });
}

/**
 * Where the box goes, from the household and its society (the orders themselves carry only ids):
 * the flat (`address.flat`), the society's name, and the captain's display name. Each is
 * undefined until it is known, or when there is none (a society has no captain until M8).
 */
export function useDeliveryPlace() {
  const household = useMyHousehold();
  const h = /** @type {{ society_id: string, address?: { flat?: string } } | null | undefined} */ (
    household.data
  );
  const society = useSociety(h?.society_id);
  const s =
    /** @type {{ name: string, captain?: { display_name?: string } | null } | undefined} */ (
      society.data
    );
  return {
    flat: h?.address?.flat,
    society: s?.name,
    captain: s?.captain?.display_name,
  };
}

/** A proposal's endpoint may not exist yet: a 404 means "nothing here", not a broken screen. @param {unknown} e */
const absent = (e) => e instanceof ApiError && e.status === 404;

/**
 * The home cook and dish of a tiffin order: the order's `tiffin` (its dishes) and `kitchen`
 * (M4 §3.1), and the kitchen's card for her name (GET /v1/partners/{id}/card, M8 §3.5). Null for
 * anything else, and until a kitchen holds the order; screens fall back to "Tiffin". The menu has
 * no thali name, so `dish.name` is the dishes joined.
 * @typedef {{ order_id: string, cook: { name: string }, dish: { name: string }, meal: "lunch" | "dinner" }} TiffinInfo
 * @param {string | undefined} id @param {string} lang
 * @returns {{ data: TiffinInfo | null | undefined }}
 */
export function useTiffinInfo(id, lang) {
  const order = useOrder(id, lang);
  const o = order.data;
  const cook = usePublicPartner(o?.kitchen?.partner_id);
  if (!o) return { data: undefined };
  if (!o.tiffin || !o.kitchen) return { data: null };
  if (cook.isError) return { data: null };
  if (!cook.data) return { data: undefined };
  return {
    data: {
      order_id: o.id,
      cook: { name: cook.data.name },
      dish: { name: o.tiffin.dishes.map((d) => d.name).join(", ") },
      meal: o.tiffin.meal,
    },
  };
}

/**
 * What the household rated a delivered tiffin (GET /v1/ratings?order_id=, M8 §3.5), or null if it
 * did not (yet). Only tiffins are rated: for a box the API says 404, which reads as null too.
 * @typedef {{ order_id: string, partner_id: string, score: number, comment: string | null, created_at: string }} RatingOut
 * @param {string | undefined} id
 */
export function useRating(id) {
  return useQuery({
    queryKey: ["orders", id, "rating"],
    queryFn: async () => {
      try {
        return /** @type {RatingOut} */ (await request(`/v1/ratings?order_id=${id}`));
      } catch (e) {
        if (absent(e)) return null;
        throw e;
      }
    },
    enabled: !!id,
    retry: false,
  });
}

/**
 * Tell the hub something went wrong with a delivery (POST /v1/orders/{id}/issues, M4 §3.2).
 * One Idempotency-Key per order and problem (keyed by both), so a retry never files it twice and
 * reporting on another order never reuses the first order's key.
 * @param {string | undefined} orderId
 */
export function useReportIssue(orderId) {
  const keys = useRef(/** @type {Record<string, string>} */ ({}));
  return useMutation({
    mutationFn: (/** @type {string} */ kind) => {
      const slot = `${orderId}:${kind}`;
      keys.current[slot] ??= crypto.randomUUID();
      return request(`/v1/orders/${orderId}/issues`, {
        method: "POST",
        body: { kind },
        headers: { "idempotency-key": keys.current[slot] },
      });
    },
  });
}

/**
 * Book a home kitchen's tiffins for a meal (POST /v1/orders, kind "tiffin", M4 §3.5): the menu
 * fixes the date, meal, price and cook; `portions` is 1 to 12. One key per menu and count, so a
 * retry never books twice; it is paid at the cut-off with the box.
 */
export function useAddTiffin() {
  const client = useQueryClient();
  const keys = useRef(/** @type {Record<string, string>} */ ({}));
  return useMutation({
    mutationFn: (/** @type {{ menuId: string, qty: number }} */ v) => {
      const id = `${v.menuId}:${v.qty}`;
      keys.current[id] ??= crypto.randomUUID();
      return request("/v1/orders", {
        method: "POST",
        body: { kind: "tiffin", menu_id: v.menuId, portions: v.qty },
        headers: { "idempotency-key": keys.current[id] },
      });
    },
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["cycle", "current"] });
      client.invalidateQueries({ queryKey: ["orders"] });
    },
  });
}

/**
 * Rate a delivered tiffin, 1 to 5 stars and a word for the staff (POST /v1/ratings, M8 §3.5): once
 * per order, from delivery until 48 hours after it. One Idempotency-Key per order, so a retry never
 * rates twice.
 */
export function useRateTiffin() {
  const client = useQueryClient();
  const keys = useRef(/** @type {Record<string, string>} */ ({}));
  return useMutation({
    mutationFn: (/** @type {{ orderId: string, score: number, comment?: string }} */ r) => {
      keys.current[r.orderId] ??= crypto.randomUUID();
      return request("/v1/ratings", {
        method: "POST",
        body: { order_id: r.orderId, score: r.score, comment: r.comment || undefined },
        headers: { "idempotency-key": keys.current[r.orderId] },
      });
    },
    onSuccess: () => client.invalidateQueries({ queryKey: ["orders"] }),
  });
}
