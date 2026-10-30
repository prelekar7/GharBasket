// Society delivery-day mocks (M3). `PUT /v1/admin/societies/{id}/delivery-days`,
// `GET /v1/societies/{id}/delivery-days` and `GET /v1/hubs/{id}/delivery-days` (§3.5, every society of the hub with
// its status) are the real contract's. Weekdays are the contract's: 0 = Monday … 6 = Sunday.
import { HttpResponse, http } from "msw";
import { SLOT_ID, SOCIETY_ID } from "./ids.js";
import { boxSlots } from "./orders.js";
import { fieldError, idempotent, mockStore, problem, roleOf } from "./util.js";

/**
 * A society's delivery days, as the real DeliveryDayIn has them: a weekday (0 = Monday) and the slot that day.
 * @typedef {{ society_id: string, name: string, area: string, status: "prospect" | "unlocking" | "live" | "paused",
 *   days: { weekday: number, slot_id: string }[] }} SocietyDays
 */
/** @type {SocietyDays[]} */
const DAYS_SEED = [
  {
    society_id: SOCIETY_ID["sunrise-towers"],
    name: "Sunrise Towers",
    area: "Sector 20, Kharghar",
    status: "live",
    days: [1, 4, 6].map((weekday) => ({ weekday, slot_id: SLOT_ID.am })),
  },
  {
    society_id: SOCIETY_ID["krishna-kunj"],
    name: "Krishna Kunj",
    area: "Sector 17, New Panvel",
    status: "live",
    days: [1, 4].map((weekday) => ({ weekday, slot_id: SLOT_ID.am })),
  },
  // Not live yet: a society can still get boxes once the hub sets its days (M3 §3.5).
  {
    society_id: SOCIETY_ID["green-valley-heights"],
    name: "Green Valley Heights",
    area: "Sector 12, Kharghar",
    status: "unlocking",
    days: [2].map((weekday) => ({ weekday, slot_id: SLOT_ID.am })),
  },
  {
    society_id: SOCIETY_ID["shree-ganesh-chs"],
    name: "Shree Ganesh CHS",
    area: "Sector 9, Kharghar",
    status: "unlocking",
    days: [],
  },
  {
    society_id: SOCIETY_ID["panchavati-residency"],
    name: "Panchavati Residency",
    area: "Sector 35, Kharghar",
    status: "prospect",
    days: [],
  },
];

/** The order of the hub read: live, unlocking, prospect, paused, then by name. */
const STATUS_ORDER = ["live", "unlocking", "prospect", "paused"];

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
  // GET /v1/hubs/{id}/delivery-days (M3 §3.5): every society of the hub, whatever its status, each with its days and
  // slots (`items: []` for a society with none). The same weekday numbering as the per-society read (0 = Monday).
  http.get("*/v1/hubs/:hub/delivery-days", ({ request, params }) => {
    if (!["admin", "hub_manager", "franchise_owner"].includes(roleOf(request) ?? ""))
      return problem(403, "auth.forbidden", "Not allowed");
    const ranked = [...days()].sort(
      (a, b) =>
        STATUS_ORDER.indexOf(a.status) - STATUS_ORDER.indexOf(b.status) ||
        a.name.localeCompare(b.name),
    );
    return HttpResponse.json(
      {
        hub_id: params.hub,
        items: ranked.map((s) => ({
          society_id: s.society_id,
          name: s.name,
          area: s.area,
          status: s.status,
          ...daysOut(s),
        })),
      },
      { headers: { "cache-control": "no-store" } },
    );
  }),

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
