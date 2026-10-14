// Weekly-cycle and society delivery-day mocks (M3). `PUT /v1/admin/societies/{id}/delivery-days` and
// `GET /v1/societies/{id}/delivery-days` are the real contract's; the two hub-level reads below are
// proposals for the console (@proposal, not in the contract yet), because the spec has no per-hub overview of
// cycles or delivery days, though §8 lists "cycle status overview per hub" as a console screen. Weekdays are
// the contract's: 0 = Monday … 6 = Sunday.
import { HttpResponse, http } from "msw";
import { SLOT_ID, SOCIETY_ID } from "./ids.js";
import { boxSlots } from "./orders.js";
import { fieldError, idempotent, mockStore, problem, roleOf } from "./util.js";

/**
 * @typedef {{ society_id: string, name: string, drafted: number, approved: number, auto_approved: number,
 *   awaiting: number, skipped: number, payment_needed: number, paused: number }} CycleRow
 */

/** @type {Record<string, { label: string, drafted_at: string, reminder_at: string, cutoff_at: string, items: CycleRow[] }>} */
const WEEKS = {
  w41: {
    label: "5 to 11 Oct",
    drafted_at: "2026-10-03T18:00:00+05:30",
    reminder_at: "2026-10-05T19:00:00+05:30",
    cutoff_at: "2026-10-05T21:00:00+05:30",
    items: [
      {
        society_id: "sunrise-towers",
        name: "Sunrise Towers",
        drafted: 44,
        approved: 33,
        auto_approved: 2,
        awaiting: 7,
        skipped: 3,
        payment_needed: 2,
        paused: 1,
      },
      {
        society_id: "krishna-kunj",
        name: "Krishna Kunj",
        drafted: 19,
        approved: 13,
        auto_approved: 1,
        awaiting: 2,
        skipped: 2,
        payment_needed: 1,
        paused: 0,
      },
    ],
  },
  w40: {
    label: "28 Sep to 4 Oct",
    drafted_at: "2026-09-26T18:00:00+05:30",
    reminder_at: "2026-09-28T19:00:00+05:30",
    cutoff_at: "2026-09-28T21:00:00+05:30",
    items: [
      {
        society_id: "sunrise-towers",
        name: "Sunrise Towers",
        drafted: 43,
        approved: 41,
        auto_approved: 3,
        awaiting: 0,
        skipped: 2,
        payment_needed: 0,
        paused: 0,
      },
      {
        society_id: "krishna-kunj",
        name: "Krishna Kunj",
        drafted: 18,
        approved: 17,
        auto_approved: 1,
        awaiting: 0,
        skipped: 1,
        payment_needed: 0,
        paused: 0,
      },
    ],
  },
};

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
  // GET /v1/hubs/{id}/cycles?week= : proposal, one row per live society.
  http.get("*/v1/hubs/:hub/cycles", ({ request }) => {
    const week = new URL(request.url).searchParams.get("week") ?? "w41";
    const w = WEEKS[week];
    if (!w) return problem(404, "subscriptions.week_not_found", "No such week");
    return HttpResponse.json({
      week,
      label: w.label,
      weeks: Object.entries(WEEKS).map(([id, x]) => ({ id, label: x.label })),
      drafted_at: w.drafted_at,
      reminder_at: w.reminder_at,
      cutoff_at: w.cutoff_at,
      items: w.items,
      not_live: ["Green Valley Heights", "Shree Ganesh CHS"],
    });
  }),

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
