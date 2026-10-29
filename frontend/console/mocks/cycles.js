// Society delivery-day mocks (M3). `PUT /v1/admin/societies/{id}/delivery-days` and
// `GET /v1/societies/{id}/delivery-days` are the real contract's; the hub-level read below is a proposal for
// the console (@proposal, not in the contract yet). Weekdays are the contract's: 0 = Monday … 6 = Sunday.
import { HttpResponse, http } from "msw";
import { SLOT_ID, SOCIETY_ID } from "./ids.js";
import { boxSlots } from "./orders.js";
import { fieldError, idempotent, mockStore, problem, roleOf } from "./util.js";

/**
 * A society's delivery days, as the real DeliveryDayIn has them: a weekday (0 = Monday) and the slot that day.
 * @typedef {{ society_id: string, name: string, area: string, days: { weekday: number, slot_id: string }[] }} SocietyDays
 */
/** @type {SocietyDays[]} */
const DAYS_SEED = [
  {
    society_id: SOCIETY_ID["sunrise-towers"],
    name: "Sunrise Towers",
    area: "Sector 20, Kharghar",
    days: [1, 4, 6].map((weekday) => ({ weekday, slot_id: SLOT_ID.am })),
  },
  {
    society_id: SOCIETY_ID["krishna-kunj"],
    name: "Krishna Kunj",
    area: "Sector 17, New Panvel",
    days: [1, 4].map((weekday) => ({ weekday, slot_id: SLOT_ID.am })),
  },
];

const NOT_LIVE = ["Green Valley Heights", "Shree Ganesh CHS", "Panchavati Residency"];

const days = () => mockStore("delivery-days", () => structuredClone(DAYS_SEED));

/** DeliveryDaysOut of a society: each day with its slot (id, label, start, end). @param {SocietyDays} s */
function daysOut(s) {
  const slots = boxSlots();
  return {
    items: [...s.days]
      .sort((a, b) => a.weekday - b.weekday)
      .map((d) => ({
        weekday: d.weekday,
        slot: slots.find((x) => x.id === d.slot_id) ?? {
          id: d.slot_id,
          label: "Slot",
          start: "08:00:00",
          end: "10:00:00",
        },
      })),
  };
}

export const cyclesHandlers = [
  // GET /v1/hubs/{id}/delivery-days : @proposal (not in the contract yet), every live society's days and slots at
  // once: { items: [{ society_id, name, area, items: DeliveryDayOut[] }], not_live }. The same
  // weekday numbering as the real per-society read (0 = Monday).
  http.get("*/v1/hubs/:hub/delivery-days", () =>
    HttpResponse.json({
      items: days().map((s) => ({
        society_id: s.society_id,
        name: s.name,
        area: s.area,
        ...daysOut(s),
      })),
      not_live: NOT_LIVE,
    }),
  ),

  // GET /v1/societies/{id}/delivery-days (M3 §3.1) → DeliveryDaysOut.
  http.get("*/v1/societies/:id/delivery-days", ({ params }) => {
    const society = days().find((d) => d.society_id === params.id);
    return society ? HttpResponse.json(daysOut(society)) : HttpResponse.json({ items: [] });
  }),

  // PUT /v1/admin/societies/{id}/delivery-days (M3 §3.1, hub manager or admin, idempotent). Body
  // DeliveryDaysIn { items: [{ weekday 0..6, slot_id }] }, 1 to 3 distinct weekdays, each an active box slot of
  // the hub → DeliveryDaysOut. 422 subscriptions.delivery_days_invalid or subscriptions.society_unknown.
  http.put("*/v1/admin/societies/:id/delivery-days", ({ request, params }) =>
    idempotent(request, (body) => {
      if (!["admin", "hub_manager"].includes(roleOf(request) ?? ""))
        return problem(403, "auth.forbidden", "Not allowed");
      /** @type {ReturnType<typeof fieldError>[]} */
      const errors = [];
      if (typeof body !== "object" || body === null || !Array.isArray(body.items))
        errors.push(fieldError("items", "Field required", "missing"));
      else {
        for (const k of Object.keys(body))
          if (k !== "items")
            errors.push(fieldError(k, "Extra inputs are not permitted", "extra_forbidden"));
        if (body.items.length < 1 || body.items.length > 3)
          errors.push(fieldError("items", "List should have between 1 and 3 items", "too_short"));
        for (const [i, it] of body.items.entries()) {
          for (const k of Object.keys(it ?? {}))
            if (k !== "weekday" && k !== "slot_id")
              errors.push(
                fieldError(
                  ["items", String(i), k],
                  "Extra inputs are not permitted",
                  "extra_forbidden",
                ),
              );
          if (!Number.isInteger(it?.weekday) || it.weekday < 0 || it.weekday > 6)
            errors.push(
              fieldError(
                `items.${i}.weekday`,
                "Input should be between 0 and 6",
                "less_than_equal",
              ),
            );
          if (typeof it?.slot_id !== "string")
            errors.push(
              fieldError(`items.${i}.slot_id`, "Input should be a valid UUID", "uuid_parsing"),
            );
        }
      }
      if (errors.length)
        return problem(422, "request.invalid", "Request validation failed", { errors });
      /** @type {{ weekday: number, slot_id: string }[]} */
      const items = body.items;
      const society = days().find((d) => d.society_id === params.id);
      if (!society)
        return problem(
          422,
          "subscriptions.society_unknown",
          "No such society, or it has no hub yet.",
        );
      if (
        new Set(items.map((i) => i.weekday)).size !== items.length ||
        items.some((i) => !boxSlots().some((s) => s.id === i.slot_id))
      )
        return problem(
          422,
          "subscriptions.delivery_days_invalid",
          "Each day needs an active box slot of this hub, and each weekday once.",
        );
      society.days = items.map(({ weekday, slot_id }) => ({ weekday, slot_id }));
      return HttpResponse.json(daysOut(society));
    }),
  ),
];
