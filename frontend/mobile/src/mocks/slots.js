// M3 §3.6 green-slot mocks (contract/openapi.json): POST /v1/cycles/{id}/slot-quotes and
// PUT /v1/cycles/{id}/slot, with the contract's refusals. The move is kept in the mock cycle's
// stored state, so Week, the order and a reload all show the new slot and the discount.
import { HttpResponse, http } from "msw";
import { availableIn, movedSlot, SLOTS, savingOf, slotItems } from "./slotData.js";
import { cycle, langOf, loadState, problem, saveState, versionOf } from "./subscriptions.js";

const QUOTE_MS = 15 * 60_000;
/** @type {Map<string, { cycle_id: string, current: string, expires: number }>} */
const quotes = new Map();

/** The cut-off and edit-window refusals both endpoints share. */
function closed(/** @type {string} */ status) {
  if (status === "locked" || status === "payment_failed")
    return problem(409, "subscriptions.cutoff_passed", "The cut-off has passed for this week");
  if (status === "skipped" || status === "cancelled")
    return problem(409, "subscriptions.not_editable", `Can't change a ${status} week`);
  return null;
}

export const slotHandlers = [
  // 201 with a new quote, 200 with the one that has not expired yet; no Idempotency-Key.
  http.post("*/v1/cycles/:id/slot-quotes", ({ request }) => {
    const state = loadState();
    const refused = closed(state.status);
    if (refused) return refused;
    const lang = langOf(request);
    const c = cycle(lang);
    const { slot_id: current } = movedSlot();
    const live = [...quotes.entries()].find(
      ([, q]) => q.cycle_id === c.id && q.current === current && q.expires > Date.now(),
    );
    const [id, q] = live ?? [
      crypto.randomUUID(),
      { cycle_id: /** @type {string} */ (c.id), current, expires: Date.now() + QUOTE_MS },
    ];
    quotes.set(id, q);
    return HttpResponse.json(
      {
        id,
        cycle_id: c.id,
        delivery_date: c.delivery_date,
        expires_at: new Date(q.expires).toISOString(),
        source: "ml",
        items: slotItems(current, lang),
      },
      { status: live ? 200 : 201, headers: { "cache-control": "no-store" } },
    );
  }),

  // Move the box: If-Match with the cycle's version, the quote the person saw, the slot they chose.
  http.put("*/v1/cycles/:id/slot", async ({ request }) => {
    const ifMatch = request.headers.get("if-match");
    if (!ifMatch)
      return problem(
        428,
        "request.precondition_required",
        "Send If-Match with the version you saw",
      );
    const state = loadState();
    const refused = closed(state.status);
    if (refused) return refused;
    if (versionOf(ifMatch) !== state.version)
      return problem(412, "subscriptions.version_conflict", "The box changed; reload it");
    const body = /** @type {{ slot_id?: string, quote_id?: string }} */ (await request.json());
    const quote = quotes.get(body.quote_id ?? "");
    const c = cycle(langOf(request));
    if (!quote || quote.cycle_id !== c.id || !SLOTS.some((s) => s.id === body.slot_id))
      return problem(
        422,
        "subscriptions.quote_invalid",
        "That quote is not for this week or this slot",
      );
    const { slot_id: current } = movedSlot();
    if (quote.expires <= Date.now() || quote.current !== current)
      return problem(409, "subscriptions.quote_expired", "Those prices have expired; ask again");
    const slotId = /** @type {string} */ (body.slot_id);
    if (slotId === current) return HttpResponse.json(c);
    const slot = /** @type {(typeof SLOTS)[number]} */ (SLOTS.find((s) => s.id === slotId));
    if (availableIn(slot) === 0) return problem(409, "orders.slot_full", "That slot is full");
    const saving = savingOf(current, slotId);
    saveState({
      ...state,
      slot_id: slotId,
      green: saving
        ? {
            quote_id: /** @type {string} */ (body.quote_id),
            saving_paise: saving,
            reason_code: slot.reason,
          }
        : null,
      version: state.version + 1,
    });
    return HttpResponse.json(cycle(langOf(request)));
  }),
];
