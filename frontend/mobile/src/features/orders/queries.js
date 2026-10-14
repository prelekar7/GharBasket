import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef } from "react";
import { useSociety } from "@/features/community/queries.js";
import { useMyHousehold } from "@/features/identity/profile.js";
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
 * @typedef {OrderSummary & { lines: OrderLine[], timeline: TimelineEntry[] }} Order
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
 * The home cook and dish of a tiffin order (GET /v1/orders/{id}/tiffin, our proposal: M4's order
 * has no cook or dish, and M8 owns the kitchens). Null for anything else; screens fall back to
 * "Tiffin". @proposal (not in the contract yet)
 * @typedef {{ order_id: string, kitchen_id: string, cook: { name: string }, dish: { name: string },
 *   meal: "lunch" | "dinner", qty: number }} TiffinInfo
 * @param {string | undefined} id @param {string} lang
 */
export function useTiffinInfo(id, lang) {
  return useQuery({
    queryKey: ["orders", id, "tiffin", lang],
    queryFn: async () => {
      try {
        return /** @type {TiffinInfo} */ (await request(`/v1/orders/${id}/tiffin`));
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
 * What the household rated a delivered order (GET /v1/orders/{id}/rating, our proposal for M4 and
 * M8), or null if it did not (yet). @proposal (not in the contract yet)
 * @typedef {{ order_id: string, score: number, tags: string[], comment: string | null }} RatingOut
 * @param {string | undefined} id
 */
export function useRating(id) {
  return useQuery({
    queryKey: ["orders", id, "rating"],
    queryFn: async () => {
      try {
        return /** @type {RatingOut} */ (await request(`/v1/orders/${id}/rating`));
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
 * Tell the hub something went wrong with a delivery (POST /v1/orders/{id}/issues).
 * @proposal (not in the contract yet)
 * One Idempotency-Key per order and problem, so a retry never files it twice.
 * @param {string | undefined} orderId
 */
export function useReportIssue(orderId) {
  const keys = useRef(/** @type {Record<string, string>} */ ({}));
  return useMutation({
    mutationFn: (/** @type {string} */ kind) => {
      keys.current[kind] ??= crypto.randomUUID();
      return request(`/v1/orders/${orderId}/issues`, {
        method: "POST",
        body: { kind },
        headers: { "idempotency-key": keys.current[kind] },
      });
    },
  });
}

/**
 * Book a home kitchen's tiffins for a meal (POST /v1/orders, kind "tiffin": M4 lists it for P3
 * without a body, so this one is our proposal). One key per day, meal and count, so a retry never
 * books twice; it is paid at the cut-off with the box. @proposal (not in the contract yet)
 */
export function useAddTiffin() {
  const client = useQueryClient();
  const keys = useRef(/** @type {Record<string, string>} */ ({}));
  return useMutation({
    mutationFn: (
      /** @type {{ date: string, slot: string, kitchenId: string, qty: number }} */ v,
    ) => {
      const id = `${v.date}:${v.slot}:${v.qty}`;
      keys.current[id] ??= crypto.randomUUID();
      return request("/v1/orders", {
        method: "POST",
        body: {
          kind: "tiffin",
          delivery_date: v.date,
          slot: v.slot,
          kitchen_id: v.kitchenId,
          qty: v.qty,
        },
        headers: { "idempotency-key": keys.current[id] },
      });
    },
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["cycle", "current"] });
      client.invalidateQueries({ queryKey: ["orders"] });
    },
  });
}

/** @typedef {{ orderId: string, score: number, tags: string[], comment?: string }} Rating */

/**
 * Rate a delivered day: the box and any tiffin with it, one rating each (POST
 * /v1/orders/{id}/rating, our proposal for M4 and M8). One Idempotency-Key per order, so a retry
 * after half went through never rates twice. @proposal (not in the contract yet)
 */
export function useRateDelivery() {
  const client = useQueryClient();
  const keys = useRef(/** @type {Record<string, string>} */ ({}));
  return useMutation({
    mutationFn: (/** @type {Rating[]} */ list) =>
      Promise.all(
        list.map((r) => {
          keys.current[r.orderId] ??= crypto.randomUUID();
          return request(`/v1/orders/${r.orderId}/rating`, {
            method: "POST",
            body: { score: r.score, tags: r.tags, comment: r.comment || undefined },
            headers: { "idempotency-key": keys.current[r.orderId] },
          });
        }),
      ),
    onSuccess: () => client.invalidateQueries({ queryKey: ["orders"] }),
  });
}
