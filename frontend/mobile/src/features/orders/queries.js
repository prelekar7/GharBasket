import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef } from "react";
import { request } from "@/lib/api.js";

/**
 * @typedef {{
 *   id: string, kind: "box" | "tiffin" | "addon", status: string, delivery_date: string, eta_by?: string,
 *   items_count?: number, total_paise: number, paid_from?: "wallet" | "upi_autopay", plan_ready_on?: string,
 *   address: { flat: string, society: string }, hub?: { name: string }, captain?: { name: string, phone_e164: string },
 *   cook?: { name: string }, dish?: { name: string }, slot?: "lunch" | "dinner", qty?: number, events: { to_status: string, at: string }[], version: number,
 * }} Order
 */

/** Polling stands in for M7's SSE stream until it exists; the real one pushes every 15 s. */
const LIVE_MS = 5000;
/** Statuses where something is moving: the van, or the captain on the way to the door. */
const MOVING = new Set(["DISPATCHED", "AT_SOCIETY", "REDELIVERY"]);

/** The household's orders, soonest first (GET /v1/orders, M4). @param {string} lang */
export function useOrders(lang) {
  return useQuery({
    queryKey: ["orders", lang],
    queryFn: async () =>
      /** @type {{ items: Order[], next_cursor: string | null }} */ (await request("/v1/orders"))
        .items,
    refetchInterval: (q) => (q.state.data?.some((o) => MOVING.has(o.status)) ? LIVE_MS : false),
  });
}

/** One order with its timeline, kept fresh while it is on the way (GET /v1/orders/{id}, M4). @param {string} id @param {string} lang */
export function useOrder(id, lang) {
  return useQuery({
    queryKey: ["orders", id, lang],
    queryFn: () => /** @type {Promise<Order>} */ (request(`/v1/orders/${id}`)),
    refetchInterval: (q) => (MOVING.has(q.state.data?.status ?? "") ? LIVE_MS : false),
  });
}

/**
 * @typedef {{ lat: number, lng: number }} Point
 * @typedef {{ order_id: string, status: string, rider: { name: string }, hub: Point & { name: string },
 *   society: Point & { name: string }, van: Point, eta_at: string, updated_at: string }} Tracking
 */

/** Where today's van is, polled while it drives (GET /v1/orders/{id}/tracking, our proposal for M7). @param {string} id @param {string} lang */
export function useTracking(id, lang) {
  return useQuery({
    queryKey: ["tracking", id, lang],
    queryFn: () => /** @type {Promise<Tracking>} */ (request(`/v1/orders/${id}/tracking`)),
    refetchInterval: (q) => (q.state.data?.status === "DISPATCHED" ? LIVE_MS : false),
  });
}

/**
 * Tell the hub something went wrong with a delivery (POST /v1/orders/{id}/issues, our proposal).
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
 * Book a home kitchen's tiffins for a meal (POST /v1/orders, kind "tiffin", M4). One key per day,
 * meal and count, so a retry never books twice; it is paid at the cut-off with the box.
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
