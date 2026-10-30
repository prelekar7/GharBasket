import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRef } from "react";
import { ApiError, request } from "@/lib/api.js";

/**
 * A box can take add-on lines while its order is still `approved`, up to the cut-off (M4 §3.1).
 * @param {{ kind: string, status: string, cutoff_at: string }} order
 * @param {number} [now] milliseconds, for tests
 */
export const addonsOpen = (order, now = Date.now()) =>
  order.kind === "box" && order.status === "approved" && now < Date.parse(order.cutoff_at);

/** @typedef {{ product_id: string, qty: number }} AddonItem qty 0 removes the line, 1 to 20 sets it */

/**
 * Add, change or remove add-on lines on an approved order (POST /v1/orders/{id}/addons, M4 §3.1:
 * Idempotency-Key and If-Match with the order's version). One key per order, version and change,
 * so a retry never applies twice; resolves to the order at its new version.
 */
export function useSetAddons() {
  const client = useQueryClient();
  const keys = useRef(/** @type {Record<string, string>} */ ({}));
  return useMutation({
    mutationFn: (/** @type {{ orderId: string, version: number, items: AddonItem[] }} */ v) => {
      const id = `${v.orderId}:${v.version}:${JSON.stringify(v.items)}`;
      keys.current[id] ??= crypto.randomUUID();
      return request(`/v1/orders/${v.orderId}/addons`, {
        method: "POST",
        body: { items: v.items },
        headers: { "idempotency-key": keys.current[id], "if-match": `"${v.version}"` },
      });
    },
    // A refusal means the order moved on (cut-off, a newer version): read it again either way.
    onSettled: () => client.invalidateQueries({ queryKey: ["orders"] }),
  });
}

/**
 * The words for why add-ons were refused (M4 §3.1 error codes), as a locale key under
 * `orders.addons`. @param {unknown} e
 */
export function addonErrorKey(e) {
  switch (e instanceof ApiError ? e.code : "") {
    case "orders.cutoff_passed":
    case "orders.not_editable":
      return "closed";
    case "request.version_mismatch":
      return "changed";
    case "orders.product_unknown":
    case "orders.no_price":
      return "unavailable";
    case "orders.qty_invalid":
      return "limits";
    default:
      return "failed";
  }
}
