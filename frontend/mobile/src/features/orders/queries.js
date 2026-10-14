import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef } from "react";
import { request } from "@/lib/api.js";

/**
 * @typedef {{
 *   id: string, kind: "box" | "tiffin" | "addon", status: string, delivery_date: string, eta_by?: string,
 *   items_count?: number, total_paise: number, paid_from?: "wallet" | "upi_autopay", plan_ready_on?: string,
 *   address: { flat: string, society: string }, hub?: { name: string }, captain?: { name: string, phone_e164: string },
 *   cook?: { name: string }, dish?: { name: string }, events: { to_status: string, at: string }[], version: number,
 * }} Order
 */

/** The household's orders, soonest first (GET /v1/orders, M4). @param {string} lang */
export function useOrders(lang) {
  return useQuery({
    queryKey: ["orders", lang],
    queryFn: async () =>
      /** @type {{ items: Order[], next_cursor: string | null }} */ (await request("/v1/orders"))
        .items,
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
 * Book a home kitchen's tiffin for a day (POST /v1/orders, kind "tiffin", M4). One key per day and
 * meal, so a retry never books twice; it is paid at the cut-off with the box.
 */
export function useAddTiffin() {
  const client = useQueryClient();
  const keys = useRef(/** @type {Record<string, string>} */ ({}));
  return useMutation({
    mutationFn: (/** @type {{ date: string, slot: string, kitchenId: string }} */ v) => {
      const id = `${v.date}:${v.slot}`;
      keys.current[id] ??= crypto.randomUUID();
      return request("/v1/orders", {
        method: "POST",
        body: { kind: "tiffin", delivery_date: v.date, slot: v.slot, kitchen_id: v.kitchenId },
        headers: { "idempotency-key": keys.current[id] },
      });
    },
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["cycle", "current"] });
      client.invalidateQueries({ queryKey: ["orders"] });
    },
  });
}
