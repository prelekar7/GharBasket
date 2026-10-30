// Tiffin board mocks (M8 §3.4, docs/modules/M8-partners.md): the real contract.
//   GET  /v1/hubs/{id}/tiffin-board?date=                 TiffinBoardOut (hub staff)
//   POST /v1/hubs/{id}/tiffin-assignments/run {date}      RunOut (hub manager, admin; Idempotency-Key)
//   POST /v1/hubs/{id}/tiffin-assignments/manual {date, order_ids, partner_id, note}   ManualOut (accepted at once)
// A day is made up the first time it is asked for: lunch has one accepted offer from Lata and two tiffins nobody could
// take (reasons as the greedy fallback gives them); dinner has two tiffins waiting for a first round. Manual assignment
// closes 2 hours before `ready_by`, so every past day is closed.
import { HttpResponse, http } from "msw";
import { deny } from "./adminShared.js";
import { KHARGHAR_HUB_ID } from "./hubs.js";
import { PARTNER_ID } from "./partner-ids.js";
import { idempotent, mockStore, mockUuid, problem } from "./util.js";

const STAFF = ["admin", "hub_manager"];
const READERS = [...STAFF, "franchise_owner"];
const READY_BY = { lunch: "11:30", dinner: "18:30" };
const CAPACITY = { lunch: 5, dinner: 6 };
const SOCIETIES = [
  { id: mockUuid("a100", 1), name: "Sunrise Towers" },
  { id: mockUuid("a100", 2), name: "Green Valley" },
];

/** @typedef {{ order_id: string, short_code: string, portions: number, product: string, society: { id: string, name: string }, status: string }} Order */
/** @typedef {{ id: string, meal: "lunch" | "dinner", partner: { id: string, display_name: string }, status: string, round: number, source: string, portions: number, ready_by: string, orders: Order[], over_capacity?: boolean }} Assignment */
/** @typedef {{ order_id: string, society: { id: string, name: string }, portions: number, meal: "lunch" | "dinner", reason: string }} Free */
/** @typedef {{ free: Free[], assignments: Assignment[], runs: Record<string, unknown>[], n: number }} Day */

/** @type {() => Map<string, Day>} */
const days = () => mockStore("tiffin-board", () => new Map());

/** @param {string} date @returns {Day} */
function day(date) {
  const all = days();
  const n = (all.size + 1) * 10;
  /** @param {number} i @param {"lunch" | "dinner"} meal @param {string} reason @param {number} portions @returns {Free} */
  const free = (i, meal, reason, portions) => ({
    order_id: mockUuid("c100", n + i),
    society: SOCIETIES[i % 2],
    portions,
    meal,
    reason,
  });
  if (!all.has(date)) {
    all.set(date, {
      n,
      free: [
        free(1, "lunch", "no_skills", 1),
        free(2, "lunch", "out_of_reach", 2),
        free(3, "dinner", "pending", 2),
        free(4, "dinner", "pending", 3),
      ],
      assignments: [
        {
          id: mockUuid("c200", n),
          meal: "lunch",
          partner: { id: PARTNER_ID.lata, display_name: "Lata tai" },
          status: "accepted",
          round: 1,
          source: "ml",
          portions: 3,
          ready_by: READY_BY.lunch,
          orders: [
            {
              order_id: mockUuid("c100", n),
              short_code: "T-01",
              portions: 3,
              product: "Diet thali",
              society: SOCIETIES[0],
              status: "assigned",
            },
          ],
        },
      ],
      runs: [
        {
          meal: "lunch",
          round: 1,
          source: "ml",
          model: "assign-v1",
          status: "done",
          gini: 0.12,
          km_per_portion: 0.8,
          at: `${date}T16:00:00Z`,
        },
      ],
    });
  }
  return /** @type {Day} */ (all.get(date));
}

/** @param {Day} d @param {"lunch" | "dinner"} meal */
const taken = (d, meal) =>
  d.assignments.filter((a) => a.meal === meal).reduce((n, a) => n + a.portions, 0);

/** @param {string} date @param {"lunch" | "dinner"} meal Manual assignment stops 2 hours before `ready_by`. */
const closed = (date, meal) => {
  const [h, m] = READY_BY[meal].split(":").map(Number);
  return Date.now() > Date.parse(`${date}T00:00:00+05:30`) + ((h - 2) * 60 + m) * 60_000;
};

/** @param {Day} d @param {"lunch" | "dinner"} meal */
const mealOut = (d, meal) => {
  const orders = d.assignments.filter((a) => a.meal === meal).flatMap((a) => a.orders);
  const left = d.free.filter((f) => f.meal === meal);
  const count = (/** @type {string} */ s) =>
    d.assignments.filter((a) => a.meal === meal && a.status === s).length;
  return {
    meal,
    orders: orders.length + left.length,
    portions: [...orders, ...left].reduce((n, o) => n + o.portions, 0),
    assigned: orders.length,
    accepted: count("accepted"),
    preparing: count("preparing"),
    ready: count("ready"),
    picked_up: orders.filter((o) => o.status === "picked_up").length,
    unassigned: left.map(({ meal: _m, ...u }) => u),
  };
};

const bad = (/** @type {string} */ field) =>
  problem(422, "request.invalid", "Request not valid", {
    errors: [{ field, message: "Not valid" }],
  });
const isDate = (/** @type {unknown} */ v) => typeof v === "string" && /^\d{4}-\d{2}-\d{2}$/.test(v);

export const tiffinBoardHandlers = [
  http.get("*/v1/hubs/:hub/tiffin-board", ({ request, params }) => {
    const refused = deny(request, READERS);
    if (refused) return refused;
    if (params.hub !== KHARGHAR_HUB_ID) return problem(403, "auth.forbidden", "Not your hub");
    const date =
      new URL(request.url).searchParams.get("date") ?? new Date().toISOString().slice(0, 10);
    if (!isDate(date)) return bad("date");
    const d = day(date);
    const meals = /** @type {const} */ (["lunch", "dinner"])
      .map((m) => mealOut(d, m))
      .filter((m) => m.orders > 0);
    return HttpResponse.json({ date, meals, assignments: d.assignments, runs: d.runs });
  }),

  http.post(
    "*/v1/hubs/:hub/tiffin-assignments/run",
    ({ request }) =>
      deny(request, STAFF) ??
      idempotent(request, (body) => {
        if (!isDate(body?.date)) return bad("date");
        const d = day(body.date);
        let offers = 0;
        for (const meal of /** @type {const} */ (["lunch", "dinner"])) {
          // Round n + 1 offers the tiffins nobody has: Lata takes those that fit her capacity, and the rest keep their reason.
          const fit = d.free.filter(
            (f) =>
              f.meal === meal &&
              f.reason === "pending" &&
              taken(d, meal) + f.portions <= CAPACITY[meal],
          );
          if (!fit.length) continue;
          const round = d.runs.filter((r) => r.meal === meal).length + 1;
          d.assignments.push({
            id: mockUuid("c200", d.n + d.assignments.length + 1),
            meal,
            partner: { id: PARTNER_ID.lata, display_name: "Lata tai" },
            status: "offered",
            round,
            source: "fallback",
            portions: fit.reduce((n, f) => n + f.portions, 0),
            ready_by: READY_BY[meal],
            orders: fit.map((f, i) => ({
              order_id: f.order_id,
              short_code: `T-${meal === "lunch" ? "1" : "2"}${i}`,
              portions: f.portions,
              product: "Diet thali",
              society: f.society,
              status: "assigned",
            })),
          });
          d.runs.push({
            meal,
            round,
            source: "fallback",
            model: "greedy",
            status: "done",
            gini: null,
            km_per_portion: null,
            at: new Date().toISOString(),
          });
          d.free = d.free.filter((f) => !fit.includes(f));
          for (const f of d.free.filter((x) => x.meal === meal)) f.reason = "no_room";
          offers++;
        }
        return HttpResponse.json({
          run_id: offers ? mockUuid("c300", d.runs.length) : null,
          run_ids: [],
          offers,
          unassigned: d.free.length,
        });
      }),
  ),

  http.post(
    "*/v1/hubs/:hub/tiffin-assignments/manual",
    ({ request }) =>
      deny(request, STAFF) ??
      idempotent(request, (body) => {
        if (
          !isDate(body?.date) ||
          !Array.isArray(body.order_ids) ||
          !body.order_ids.length ||
          !body.partner_id
        )
          return bad("order_ids");
        if (body.partner_id !== PARTNER_ID.lata)
          return problem(404, "partners.kitchen_not_found", "Kitchen not found");
        const d = day(body.date);
        const chosen = d.free.filter((f) => body.order_ids.includes(f.order_id));
        const meal = chosen[0]?.meal;
        if (chosen.length !== body.order_ids.length || chosen.some((c) => c.meal !== meal))
          return problem(409, "partners.order_not_assignable", "Orders not assignable", {
            order_ids: body.order_ids,
          });
        if (closed(body.date, meal))
          return problem(409, "partners.assignment_closed", "Manual assignment has closed");
        const portions = chosen.reduce((n, c) => n + c.portions, 0);
        /** @type {Assignment} */
        const a = {
          id: mockUuid("c200", d.n + d.assignments.length + 1),
          meal,
          partner: { id: PARTNER_ID.lata, display_name: "Lata tai" },
          status: "accepted",
          round: d.runs.length || 1,
          source: "manual",
          portions,
          ready_by: READY_BY[meal],
          over_capacity: taken(d, meal) + portions > CAPACITY[meal],
          orders: chosen.map((c, i) => ({
            order_id: c.order_id,
            short_code: `T-9${i}`,
            portions: c.portions,
            product: "Diet thali",
            society: c.society,
            status: "assigned",
          })),
        };
        d.assignments.push(a);
        d.free = d.free.filter((f) => !chosen.includes(f));
        return HttpResponse.json({ ...a, over_capacity: a.over_capacity === true });
      }),
  ),
];
