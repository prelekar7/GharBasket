// Waste and surplus cascade mocks (M6 §3, US-6 and US-7). Spec: GET / POST /v1/hubs/{id}/waste-logs
// (POST needs an Idempotency-Key); waste_logs columns: product_id, qty, reason (spoilage, damage,
// surplus), cascade_to (imperfect, kitchen, ngo, bin), at. The `12:30` surplus_cascade job writes the
// first four entries, seeded after the Waste design (Tue 6 Oct 2026, 12:35 PM). Proposals of ours:
//   - `unit`, `why` and `context` on a log, `cascade_detail` (who got it), and the metrics endpoint
//     GET /v1/hubs/{id}/waste-metrics (waste per 100 kg sold, "computed daily per hub", US-7);
//   - a log made by hand is routed by its reason: spoilage and damage go to compost (`bin`), surplus
//     is listed as Imperfect Sabzi first (the next 12:30 run moves what is unsold down the cascade).
// The LLM and the console never compute these numbers; the mock stands in for the API doing it.
import { HttpResponse, http } from "msw";
import { catalogStore } from "./catalog.js";
import { idempotent, mockStore, problem } from "./util.js";

const DATE = "2026-10-06";
/** kg sold today: 0.7 kg of compost is 1.9 per 100 kg sold. */
const SOLD_KG = 36.84;
const REASONS = ["spoilage", "damage", "surplus"];
const UNITS = ["kg", "bunch", "piece"];
/** Per 100 kg sold on the four delivery days before today, and the week-ago figure. */
const BEFORE = [2.8, 2.6, 2.3, 2.1];
const LAST_WEEK = 2.3;
/** kg of compost and spoilage on the delivery days before today. */
const DAYS = [
  ["2026-09-29", 1.2],
  ["2026-10-02", 1.0],
  ["2026-10-04", 0.9],
];

/**
 * @typedef {{ id: string, product_id: string, name: string, context: string | null, qty: number,
 *   unit: string, reason: "spoilage" | "damage" | "surplus", why: string,
 *   cascade_to: "imperfect" | "kitchen" | "ngo" | "bin", cascade_detail: string | null, at: string }} WasteLog
 */

/** @type {() => { logs: WasteLog[] }} */
const state = () =>
  mockStore("waste", () => {
    /** @type {WasteLog[]} */
    const logs = [
      {
        id: "w1",
        product_id: "tomato",
        name: "Tomato",
        context: "Grade B at receiving",
        qty: 2.4,
        unit: "kg",
        reason: "surplus",
        why: "Soft, still good for cooking",
        cascade_to: "imperfect",
        cascade_detail: null,
        at: `${DATE}T12:31:00+05:30`,
      },
      {
        id: "w2",
        product_id: "palak",
        name: "Spinach (palak)",
        context: "Ordered with buffer",
        qty: 6,
        unit: "bunch",
        reason: "surplus",
        why: "Surplus after packing",
        cascade_to: "kitchen",
        cascade_detail: "Lata Shinde",
        at: `${DATE}T12:32:00+05:30`,
      },
      {
        id: "w3",
        product_id: "bhindi",
        name: "Bhindi",
        context: "48 h shelf life ends today",
        qty: 1.5,
        unit: "kg",
        reason: "surplus",
        why: "Surplus after packing",
        cascade_to: "ngo",
        cascade_detail: null,
        at: `${DATE}T12:33:00+05:30`,
      },
      {
        id: "w4",
        product_id: "coriander",
        name: "Coriander (dhania)",
        context: "Wilted",
        qty: 0.7,
        unit: "kg",
        reason: "spoilage",
        why: "Spoiled",
        cascade_to: "bin",
        cascade_detail: null,
        at: `${DATE}T12:34:00+05:30`,
      },
    ];
    return { logs };
  });

/** Compost and spoilage kg for today: what counts as waste (cascaded food does not). */
const wastedKg = () =>
  +state()
    .logs.filter((l) => l.cascade_to === "bin" && l.unit === "kg")
    .reduce((n, l) => n + l.qty, 0)
    .toFixed(2);

export const wasteHandlers = [
  http.get("*/v1/hubs/:id/waste-logs", ({ params }) => {
    if (params.id !== "kharghar") return problem(404, "hubs.not_found", "No such hub");
    return HttpResponse.json({
      date: DATE,
      as_of: "12:35 PM",
      items: [...state().logs].sort((a, b) => a.at.localeCompare(b.at)),
      next_cursor: null,
    });
  }),

  http.get("*/v1/hubs/:id/waste-metrics", ({ params }) => {
    if (params.id !== "kharghar") return problem(404, "hubs.not_found", "No such hub");
    const kg = wastedKg();
    const per100 = +((kg / SOLD_KG) * 100).toFixed(1);
    return HttpResponse.json({
      waste_per_100kg: per100,
      last_week_per_100kg: LAST_WEEK,
      trend: [...BEFORE, per100],
      daily_kg: [...DAYS.map(([date, value]) => ({ date, kg: value })), { date: DATE, kg }],
    });
  }),

  // POST /v1/hubs/{id}/waste-logs  { product_id, qty, unit, reason, note? }
  http.post("*/v1/hubs/:id/waste-logs", ({ params, request }) =>
    idempotent(request, (body) => {
      if (params.id !== "kharghar") return problem(404, "hubs.not_found", "No such hub");
      const product = catalogStore().products.find((p) => p.slug === body?.product_id);
      /** @type {{ field: string, message: string }[]} */
      const errors = [];
      if (!product) errors.push({ field: "product_id", message: "Choose an item" });
      if (!(typeof body?.qty === "number" && body.qty > 0 && body.qty <= 999))
        errors.push({ field: "qty", message: "Enter an amount above 0" });
      if (!UNITS.includes(body?.unit)) errors.push({ field: "unit", message: "Choose a unit" });
      if (!REASONS.includes(body?.reason))
        errors.push({ field: "reason", message: "Choose a reason" });
      if (errors.length || !product)
        return problem(422, "request.invalid", "Check the details", { errors });
      const surplus = body.reason === "surplus";
      /** @type {WasteLog} */
      const log = {
        id: `w${state().logs.length + 1}`,
        product_id: product.slug,
        name: product.names.en,
        context: null,
        qty: body.qty,
        unit: body.unit,
        reason: body.reason,
        why:
          String(body.note ?? "").trim() ||
          { spoilage: "Spoiled", damage: "Damaged", surplus: "Surplus" }[
            /** @type {"spoilage"} */ (body.reason)
          ],
        cascade_to: surplus ? "imperfect" : "bin",
        cascade_detail: null,
        at: new Date().toISOString(),
      };
      state().logs.push(log);
      return HttpResponse.json(log, { status: 201 });
    }),
  ),
];
