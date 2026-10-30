import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ApiError, request } from "@/lib/api.js";
import { GREEN_SLOTS } from "@/lib/features.js";
import { ifMatch } from "./queries.js";

/**
 * Green delivery slots (M3 §3.6, M10 §3.8, contract/openapi.json). Money is integer paise.
 * @typedef {{ slot: import("./queries.js").Slot, current: boolean, available: number, green: boolean, saving_paise: number, rank: number | null, reason_code: "same_society" | "neighbour_stop" | "new_stop" | "new_trip" | "unpriced", reason_params: { metres?: number } | null }} SlotOption
 * @typedef {{ id: string, cycle_id: string, delivery_date: string, expires_at: string, source: "ml" | "fallback" | "unpriced", items: SlotOption[] }} SlotQuote
 * A quote lasts 15 minutes. `source` is "fallback" when the ML service was slow (same rules, same
 * money), and "unpriced" when the day has no tentative routes: every saving is then 0.
 */

const OFF = () => Promise.reject(new Error("Green slots are off in this build"));

/** Every box slot that date with its saving (POST /v1/cycles/{id}/slot-quotes; moves no money, so no Idempotency-Key). @param {string} cycleId */
export const quoteSlots = (cycleId) =>
  GREEN_SLOTS
    ? /** @type {Promise<SlotQuote>} */ (
        request(`/v1/cycles/${cycleId}/slot-quotes`, { method: "POST", body: {} })
      )
    : OFF();

/**
 * Move the box to a quoted slot (PUT /v1/cycles/{id}/slot with If-Match, the cycle's version). It
 * moves no money either, so the version is the only guard; a retry of the same move is harmless.
 * @param {{ cycle: import("./queries.js").Cycle, quoteId: string, slotId: string }} v
 */
export const moveSlot = ({ cycle, quoteId, slotId }) =>
  GREEN_SLOTS
    ? /** @type {Promise<import("./queries.js").Cycle>} */ (
        request(`/v1/cycles/${cycle.id}/slot`, {
          method: "PUT",
          headers: { "if-match": ifMatch(cycle) },
          body: { slot_id: slotId, quote_id: quoteId },
        })
      )
    : OFF();

/** The quote for a cycle, asked for on demand (call `mutate` when the sheet opens, and again to re-quote). */
export const useSlotQuote = () => useMutation({ mutationFn: quoteSlots });

/** Move the box to a slot; on success the cycle and the orders are read again so the slot and the discount show. */
export function useMoveSlot() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: moveSlot,
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["cycle"] });
      client.invalidateQueries({ queryKey: ["orders"] });
    },
  });
}

/**
 * What a refused quote or move means for the person: the locale key under `slots.err`, and whether
 * to ask for a fresh quote (`requote`), read the week again (`reload`) or close the sheet (`close`).
 * @param {unknown} e
 */
export function slotProblem(e) {
  const code = e instanceof ApiError ? e.code : "";
  switch (code) {
    case "subscriptions.cutoff_passed":
    case "subscriptions.not_editable":
      return { key: "closed", close: true, reload: true };
    case "subscriptions.green_slots_off":
      return { key: "off", close: true };
    case "orders.slot_full":
      return { key: "full", requote: true };
    case "subscriptions.quote_expired":
      return { key: "expired", requote: true };
    case "subscriptions.quote_invalid":
    case "subscriptions.version_conflict":
    case "request.precondition_required":
      return { key: "changed", requote: true, reload: true };
    default:
      return { key: "failed" };
  }
}
