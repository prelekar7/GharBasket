// Franchise-owner mocks (M12 section 3.4): the hub's saved assumptions (GET/PUT with an ETag and If-Match),
// the unit-economics calculator's measured inputs and the monthly P&L, as the frozen contract has them.
// Hub managers get no P&L or unit economics (the franchise's business): 403 auth.forbidden. The numbers
// are simulator output (`synthetic`); the pilot has no data yet, so `real` answers with zero boxes.
import { HttpResponse, http } from "msw";
import { HUBS, visibleHubs } from "./hubs.js";
import {
  buildPnl,
  buildUnitEconomics,
  isPeriod,
  KEYS,
  RANGES,
  SEED_ASSUMPTIONS,
} from "./pnl-data.js";
import { fieldError, mockStore, problem, roleOf } from "./util.js";

/** @typedef {import("../lib/unit-economics.js").Assumptions} Assumptions */

/** The refresh job's last finish, for `as_of`. */
const AS_OF = "2026-10-06T08:15:00+05:30";

const store = () =>
  mockStore("franchise-economics", () => ({
    assumptions: { ...SEED_ASSUMPTIONS },
    version: 2,
    /** @type {{ at: string, by: { name: string }, changes: { key: string, from: number, to: number }[] }[]} newest first */
    history: [
      {
        at: "2026-09-06T10:12:00+05:30",
        by: { name: "Sanjay Mehta" },
        changes: [{ key: "delivery_paise", from: 4500, to: 4000 }],
      },
    ],
  }));

/**
 * Franchise owners (their hub) and admins; a hub manager does not (M12 section 3.4).
 * @param {Request} request
 * @param {string | null} hubId
 * @returns {Response | null}
 */
function denied(request, hubId) {
  const role = roleOf(request);
  if (!role) return problem(401, "auth.unauthenticated", "Sign in again");
  if (role !== "franchise_owner" && role !== "admin") {
    return problem(403, "auth.forbidden", "This is for franchise owners");
  }
  if (!hubId) return problem(403, "auth.forbidden", "Choose a hub");
  if (!HUBS.some((h) => h.id === hubId)) {
    return problem(404, "hub_ops.hub_not_found", "No such hub");
  }
  return visibleHubs(request).some((h) => h.id === hubId)
    ? null
    : problem(403, "auth.forbidden", "You do not have access to this hub");
}

/** @param {string} hubId */
const assumptionsBody = (hubId) => ({
  hub_id: hubId,
  assumptions: store().assumptions,
  version: store().version,
  history: store().history.slice(0, 20),
});

/** The ETag of M0: the version in quotes. */
const etag = () => ({ headers: { etag: `"${store().version}"` } });

/** @param {string | null} source @returns {Response | null} */
function needsSource(source) {
  return source === "real" || source === "synthetic"
    ? null
    : problem(422, "analytics.data_source_required", "Choose real or synthetic data", {
        errors: [{ loc: ["query", "data_source"], message: "Field required", type: "missing" }],
      });
}

/** The first day of a month `back` months before `now`, as "YYYY-MM". @param {Date} now @param {number} back */
const monthsAgo = (now, back) => {
  const d = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - back, 1));
  return d.toISOString().slice(0, 7);
};

export const franchiseHandlers = [
  // GET /v1/analytics/hubs/{hub_id}/assumptions
  http.get("*/v1/analytics/hubs/:hubId/assumptions", ({ params, request }) => {
    const no = denied(request, String(params.hubId));
    return no ?? HttpResponse.json(assumptionsBody(String(params.hubId)), etag());
  }),

  // PUT /v1/analytics/hubs/{hub_id}/assumptions: If-Match, all seven keys, audit-logged
  http.put("*/v1/analytics/hubs/:hubId/assumptions", async ({ params, request }) => {
    const hubId = String(params.hubId);
    const no = denied(request, hubId);
    if (no) return no;
    const s = store();
    const match = request.headers.get("if-match");
    if (!match) {
      return problem(
        428,
        "request.precondition_required",
        "Send If-Match with the version you saw",
      );
    }
    if (match !== `"${s.version}"`) {
      return problem(
        412,
        "request.precondition_failed",
        "Someone changed these since you opened them",
      );
    }
    const body = /** @type {Record<string, any>} */ (await request.json());
    const errors = KEYS.flatMap((k) => {
      const [lo, hi] = RANGES[k];
      return Number.isSafeInteger(body?.[k]) && body[k] >= lo && body[k] <= hi
        ? []
        : [fieldError(k, `Enter a whole number from ${lo} to ${hi}`)];
    });
    if (errors.length) {
      return problem(422, "analytics.assumptions_invalid", "Check the highlighted numbers", {
        errors,
      });
    }
    const next = /** @type {Assumptions} */ (Object.fromEntries(KEYS.map((k) => [k, body[k]])));
    const changes = KEYS.filter((k) => next[k] !== s.assumptions[k]).map((key) => ({
      key,
      from: s.assumptions[key],
      to: next[key],
    }));
    if (changes.length) {
      s.history.unshift({
        at: new Date().toISOString(),
        by: { name: roleOf(request) === "admin" ? "Neha Iyer" : "Sanjay Mehta" },
        changes,
      });
      s.assumptions = next;
      s.version += 1;
    }
    return HttpResponse.json(assumptionsBody(hubId), etag());
  }),

  // GET /v1/analytics/unit-economics?hub_id=&from=&to=&data_source=&prefer=measured&aov_paise=&…
  http.get("*/v1/analytics/unit-economics", ({ request }) => {
    const q = new URL(request.url).searchParams;
    const no = denied(request, q.get("hub_id"));
    if (no) return no;
    const bad = needsSource(q.get("data_source"));
    if (bad) return bad;
    const from = q.get("from") ?? "2026-09-07";
    const to = q.get("to") ?? "2026-10-04";
    if (Date.parse(to) < Date.parse(from)) {
      return problem(422, "analytics.range_invalid", "The end date is before the start");
    }
    /** @type {Partial<Assumptions> & { delivery_fee_paise?: number }} */
    const overrides = {};
    for (const k of [...KEYS, "delivery_fee_paise"]) {
      const n = Number(q.get(k));
      if (q.get(k) !== null && Number.isSafeInteger(n)) /** @type {any} */ (overrides)[k] = n;
    }
    const weeks = Math.max(
      1,
      Math.min(8, Math.round((Date.parse(to) - Date.parse(from)) / 604_800_000)),
    );
    const real = q.get("data_source") === "real";
    const e = buildUnitEconomics({
      saved: store().assumptions,
      weeks: real ? 0 : weeks,
      prefer: q.get("prefer") === "assumed" ? "assumed" : "measured",
      overrides,
    });
    return HttpResponse.json({
      hub_id: q.get("hub_id"),
      data_source: q.get("data_source"),
      from,
      to,
      as_of: AS_OF,
      ...e,
    });
  }),

  // GET /v1/analytics/pnl?hub_id=&period=2026-09&data_source=
  http.get("*/v1/analytics/pnl", ({ request }) => {
    const q = new URL(request.url).searchParams;
    const no = denied(request, q.get("hub_id"));
    if (no) return no;
    const bad = needsSource(q.get("data_source"));
    if (bad) return bad;
    const period = q.get("period");
    if (!period || !isPeriod(period)) {
      return problem(422, "request.invalid", "period is YYYY-MM", {
        errors: [
          {
            loc: ["query", "period"],
            message: "Input should be a month like 2026-09",
            type: "value_error",
          },
        ],
      });
    }
    const now = new Date(Date.now() + 5.5 * 3_600_000);
    if (period > monthsAgo(now, 0) || period < monthsAgo(now, 23)) {
      return problem(422, "analytics.range_invalid", "Pick one of the last 24 months");
    }
    const real = q.get("data_source") === "real";
    const saved = store().assumptions;
    const pnl = buildPnl(real ? "none" : period, saved);
    const [y, m] = period.split("-").map(Number);
    const earlier = buildPnl(real ? "none" : monthsAgo(new Date(Date.UTC(y, m - 1, 1)), 1), saved);
    const e = buildUnitEconomics({ saved, weeks: real ? 0 : 4, prefer: "measured", overrides: {} });
    return HttpResponse.json({
      hub_id: q.get("hub_id"),
      ...pnl,
      period,
      as_of: AS_OF,
      previous_contribution_paise: earlier.boxes > 0 ? earlier.contribution_paise : null,
      per_box_paise: e.per_box.contribution_paise,
    });
  }),
];
