// Weekly-cycle and society delivery-day mocks (M3). `PUT /v1/admin/societies/{id}/delivery-days` is the
// spec's; the two hub-level reads below are proposals for the console, because the spec only has
// `GET /v1/societies/{id}/delivery-days` (one society) and no per-hub cycle overview, though §8 lists
// "cycle status overview per hub" as a console screen. Weekdays are ISO numbers, Mon = 1 … Sun = 7.
import { HttpResponse, http } from "msw";
import { boxSlots } from "./orders.js";
import { idempotent, mockStore, problem, roleOf } from "./util.js";

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

/** @type {{ society_id: string, name: string, area: string, slot_id: string, weekdays: number[] }[]} */
const DAYS_SEED = [
  {
    society_id: "sunrise-towers",
    name: "Sunrise Towers",
    area: "Sector 20, Kharghar",
    slot_id: "slot-am",
    weekdays: [2, 5, 7],
  },
  {
    society_id: "krishna-kunj",
    name: "Krishna Kunj",
    area: "Sector 17, New Panvel",
    slot_id: "slot-am",
    weekdays: [2, 5],
  },
];

const NOT_LIVE = ["Green Valley Heights", "Shree Ganesh CHS", "Panchavati Residency"];

const days = () => mockStore("delivery-days", () => structuredClone(DAYS_SEED));

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

  // GET /v1/hubs/{id}/delivery-days : proposal, every live society's days and slot at once.
  http.get("*/v1/hubs/:hub/delivery-days", () =>
    HttpResponse.json({
      items: days(),
      slots: boxSlots(),
      not_live: NOT_LIVE,
      needs_hub: ["Sai Darshan Apartments"],
    }),
  ),

  // PUT /v1/admin/societies/{id}/delivery-days (M3 §3, hub manager or admin, idempotent).
  // Body: { slot_id, weekdays }. Applies from the next week Autopilot plans.
  http.put("*/v1/admin/societies/:id/delivery-days", ({ request, params }) =>
    idempotent(request, (body) => {
      if (!["admin", "hub_manager"].includes(roleOf(request) ?? ""))
        return problem(403, "auth.forbidden", "Not allowed");
      const society = days().find((d) => d.society_id === params.id);
      if (!society) return problem(404, "subscriptions.society_not_found", "No such society");
      /** @type {{ field: string, message: string }[]} */
      const errors = [];
      const weekdays = Array.isArray(body?.weekdays) ? [...new Set(body.weekdays)] : [];
      if (weekdays.length === 0 || weekdays.some((d) => !Number.isInteger(d) || d < 1 || d > 7))
        errors.push({ field: "weekdays", message: "Pick at least one delivery day." });
      if (!boxSlots().some((s) => s.id === body?.slot_id))
        errors.push({ field: "slot_id", message: "Pick a slot that carries boxes." });
      if (errors.length)
        return problem(422, "request.invalid", "Check the delivery days", { errors });
      society.slot_id = body.slot_id;
      society.weekdays = /** @type {number[]} */ (weekdays).sort((a, b) => a - b);
      return HttpResponse.json(society);
    }),
  ),
];
