// Franchise-owner mocks (M12 US-2): P&L and the unit-economics calculator's saved assumptions.
// GET /v1/analytics/unit-economics is in the M12 spec; the P&L path and the PUT that saves
// assumptions are our proposals until the analytics endpoints land. Shapes are in the JSDoc of
// app/(console)/(franchise)/data.js.
import { HttpResponse, http } from "msw";
import { toDecimalText } from "../lib/money.js";
import { boxEconomics } from "../lib/unit-economics.js";
import { buildPnl, PERIOD_OPTIONS, SEED_ASSUMPTIONS } from "./pnl-data.js";
import { mockStore, problem, roleOf } from "./util.js";

/** @typedef {import("../lib/unit-economics.js").Assumptions} Assumptions */
/** @typedef {{ at: string, by: string, change: string }} AssumptionChange */

const FIELDS = /** @type {(keyof Assumptions)[]} */ ([
  "aov_paise",
  "margin_bp",
  "delivery_paise",
  "packing_paise",
  "captain_paise",
  "payment_fee_bp",
  "cac_paise",
]);

const store = () =>
  mockStore("franchise-economics", () => ({
    assumptions: { ...SEED_ASSUMPTIONS },
    /** @type {AssumptionChange[]} newest first */
    history: [
      { at: "2026-09-06T10:12:00+05:30", by: "Sanjay Mehta", change: "Delivery per box ₹45 → ₹40" },
    ],
  }));

/** Franchise owners and admins read these; a hub manager does not (M12 section 3). @param {Request} request */
function forbidden(request) {
  const role = roleOf(request);
  return role === "franchise_owner" || role === "admin"
    ? null
    : problem(403, "analytics.forbidden", "This page is for franchise owners");
}

/** @param {Assumptions} a */
const payload = (a) => ({
  hub_id: "kharghar",
  assumptions: a,
  per_box: boxEconomics(a),
  history: store().history,
});

/** The names of the assumptions in change notes. */
const NAMES = /** @type {Record<keyof Assumptions, string>} */ ({
  aov_paise: "Average box value",
  margin_bp: "Margin",
  delivery_paise: "Delivery per box",
  packing_paise: "Packing per box",
  captain_paise: "Captain commission",
  payment_fee_bp: "Payment fee",
  cac_paise: "Acquisition per box",
});

/** @param {keyof Assumptions} k @param {number} v */
function show(k, v) {
  return k.endsWith("_bp") ? `${toDecimalText(v)}%` : `₹${toDecimalText(v)}`;
}

export const franchiseHandlers = [
  // GET /v1/analytics/pnl?hub_id=&period=2026-09
  http.get("*/v1/analytics/pnl", ({ request }) => {
    const denied = forbidden(request);
    if (denied) return denied;
    const period = new URL(request.url).searchParams.get("period") ?? PERIOD_OPTIONS[0].value;
    const pnl = buildPnl(period, store().assumptions);
    if (!pnl) return problem(404, "analytics.period_not_found", "No such month");
    const index = PERIOD_OPTIONS.findIndex((p) => p.value === period);
    const earlier = PERIOD_OPTIONS[index + 1];
    const previous = earlier ? buildPnl(earlier.value, store().assumptions) : null;
    return HttpResponse.json({
      hub_id: "kharghar",
      ...pnl,
      periods: PERIOD_OPTIONS,
      previous_contribution_paise: previous?.contribution_paise ?? null,
      per_box_paise: boxEconomics(pnl.assumptions).contribution_paise,
    });
  }),

  // GET /v1/analytics/unit-economics?hub_id=
  http.get("*/v1/analytics/unit-economics", ({ request }) => {
    const denied = forbidden(request);
    return denied ?? HttpResponse.json(payload(store().assumptions));
  }),

  // PUT /v1/hubs/{hub}/unit-economics-assumptions: saved assumptions feed the P&L; the change is logged.
  http.put("*/v1/hubs/:hub/unit-economics-assumptions", async ({ request }) => {
    const denied = forbidden(request);
    if (denied) return denied;
    const body = /** @type {Record<string, any>} */ (await request.json());
    const bad = FIELDS.filter((k) => !Number.isSafeInteger(body?.[k]) || body[k] < 0);
    if (body?.margin_bp > 10_000 || body?.payment_fee_bp > 10_000) bad.push("margin_bp");
    if (bad.length) {
      return problem(422, "request.invalid", "Check the highlighted numbers", {
        errors: bad.map((k) => ({ field: k, detail: "Enter a whole, non-negative amount" })),
      });
    }
    const s = store();
    const next = /** @type {Assumptions} */ (Object.fromEntries(FIELDS.map((k) => [k, body[k]])));
    const changed = FIELDS.filter((k) => next[k] !== s.assumptions[k]);
    if (changed.length) {
      const who = roleOf(request) === "admin" ? "Neha Iyer" : "Sanjay Mehta";
      s.history.unshift({
        at: new Date().toISOString(),
        by: who,
        change: changed
          .map((k) => `${NAMES[k]} ${show(k, s.assumptions[k])} → ${show(k, next[k])}`)
          .join("; "),
      });
    }
    s.assumptions = next;
    return HttpResponse.json(payload(next));
  }),
];
