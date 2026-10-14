import { useMutation, useQuery } from "@tanstack/react-query";
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
 * Tell the hub something went wrong with a delivery (POST /v1/orders/{id}/issues, proposed in M4 §3).
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
