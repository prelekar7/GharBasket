import { useMutation } from "@tanstack/react-query";
import { useRef } from "react";
import { ApiError, request } from "@/lib/api.js";

export const SIGNALS = /** @type {const} */ (["ran_out", "wasted", "just_right"]);
/** @typedef {(typeof SIGNALS)[number]} Signal */

/**
 * "Ran out, wasted or just right" per item of a delivered box (POST /v1/cycles/{id}/feedback,
 * M3 §3.1: Idempotency-Key, from delivery day for 14 days, items of that box). One key per cycle
 * and answer, so a retry never records twice; a changed answer is a new intent.
 * Resolves to `{ recorded }`.
 */
export function useSendFeedback() {
  const keys = useRef(/** @type {Record<string, string>} */ ({}));
  return useMutation({
    mutationFn: (
      /** @type {{ cycleId: string, items: { product_id: string, signal: Signal }[] }} */ v,
    ) => {
      const id = `${v.cycleId}:${JSON.stringify(v.items)}`;
      keys.current[id] ??= crypto.randomUUID();
      return /** @type {Promise<{ recorded: number }>} */ (
        request(`/v1/cycles/${v.cycleId}/feedback`, {
          method: "POST",
          body: { items: v.items },
          headers: { "idempotency-key": keys.current[id] },
        })
      );
    },
  });
}

/** The words for why feedback was refused, as a locale key under `orders.feedback`. @param {unknown} e */
export function feedbackErrorKey(e) {
  switch (e instanceof ApiError ? e.code : "") {
    case "subscriptions.feedback_window_closed":
      return "closed";
    case "subscriptions.product_unknown":
      return "unknown";
    default:
      return "failed";
  }
}

const SENT_KEY = "gb-feedback-sent";
/** Whether this phone already sent feedback for the cycle (a convenience: the API takes later answers too). @param {string} cycleId */
export function feedbackSent(cycleId) {
  try {
    return (JSON.parse(localStorage.getItem(SENT_KEY) ?? "[]") ?? []).includes(cycleId);
  } catch {
    return false;
  }
}
/** @param {string} cycleId */
export function markFeedbackSent(cycleId) {
  try {
    localStorage.setItem(
      SENT_KEY,
      JSON.stringify(
        [...new Set([cycleId, ...JSON.parse(localStorage.getItem(SENT_KEY) ?? "[]")])].slice(0, 20),
      ),
    );
  } catch {
    // Private mode or full storage: the button simply shows again.
  }
}
