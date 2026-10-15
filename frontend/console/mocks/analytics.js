// Analytics mocks (M12 section 3.3 and 3.5): KPIs, weekly cohorts and the per-society aggregates, as the
// frozen contract has them. `data_source` is required and is `real` or `synthetic`; the numbers are simulator
// output (`synthetic`), the pilot has no data yet, so `real` answers empty. The two are never merged.
// Leaving `hub_id` out asks for every hub: admins only (403 auth.forbidden for anyone else).
import { HttpResponse, http } from "msw";
import { KHARGHAR_HUB_ID, TALOJA_HUB_ID, visibleHubs } from "./hubs.js";
import { SOCIETY_ID } from "./ids.js";
import { problem, roleOf } from "./util.js";

/** The last 12 delivery weeks (ISO Mondays) up to the week of 28 Sep 2026, oldest first. */
const WEEKS = Array.from({ length: 12 }, (_, i) =>
  new Date(Date.UTC(2026, 6, 13) + i * 7 * 86_400_000).toISOString().slice(0, 10),
);

/** One week per entry, oldest first; Kharghar's last four are the figures of the KPI design. */
const KHARGHAR = {
  active_households: [24, 28, 31, 35, 38, 41, 45, 49, 52, 55, 58, 64],
  weeks_accepted_bp: [8100, 8200, 8300, 8400, 8450, 8550, 8600, 8650, 8700, 8800, 8900, 9100],
  waste_g_per_100kg: [4600, 4400, 4100, 3900, 3600, 3400, 3100, 2900, 2800, 2600, 2300, 1900],
  on_time_bp: [9300, 9400, 9400, 9500, 9500, 9600, 9600, 9600, 9600, 9700, 9700, 9700],
  partner_earnings_paise: [
    310_000, 360_000, 420_000, 470_000, 510_000, 540_000, 580_000, 610_000, 620_000, 690_000,
    733_000, 845_000,
  ],
};

/** Taloja opened later and is smaller, so its rates are lower and still improving. */
const TALOJA = {
  active_households: [6, 8, 9, 11, 13, 15, 17, 18, 20, 22, 24, 27],
  weeks_accepted_bp: [7600, 7700, 7800, 7900, 8000, 8100, 8200, 8250, 8300, 8350, 8400, 8500],
  waste_g_per_100kg: [6100, 5800, 5500, 5200, 4900, 4600, 4300, 4000, 3700, 3400, 3100, 2800],
  on_time_bp: [9000, 9100, 9100, 9200, 9200, 9300, 9300, 9400, 9400, 9400, 9500, 9500],
  partner_earnings_paise: [
    90_000, 110_000, 125_000, 140_000, 160_000, 175_000, 190_000, 210_000, 230_000, 250_000,
    265_000, 300_000,
  ],
};

/** @typedef {typeof KHARGHAR} Series */
/** @type {Record<string, Series>} */
const BY_HUB = { [KHARGHAR_HUB_ID]: KHARGHAR, [TALOJA_HUB_ID]: TALOJA };

/**
 * All hubs together: households and earnings add up; the rates are averaged with the hubs' active
 * households as weights (a bigger hub counts for more), rounded to whole basis points and to 100 g.
 * @returns {Series}
 */
function allHubs() {
  const hubs = Object.values(BY_HUB);
  const sum = (/** @type {number[][]} */ rows) =>
    rows[0].map((_, i) => rows.reduce((n, r) => n + r[i], 0));
  /**
   * @param {(s: Series) => number[]} pick
   * @param {number} step what the result is rounded to
   */
  const weighted = (pick, step = 1) =>
    KHARGHAR.active_households.map((_, i) => {
      const w = hubs.reduce((n, h) => n + h.active_households[i], 0);
      return (
        step *
        Math.round(hubs.reduce((n, h) => n + pick(h)[i] * h.active_households[i], 0) / w / step)
      );
    });
  return {
    active_households: sum(hubs.map((h) => h.active_households)),
    weeks_accepted_bp: weighted((h) => h.weeks_accepted_bp),
    waste_g_per_100kg: weighted((h) => h.waste_g_per_100kg, 100),
    on_time_bp: weighted((h) => h.on_time_bp),
    partner_earnings_paise: sum(hubs.map((h) => h.partner_earnings_paise)),
  };
}

/** The refresh job's last finish, for `as_of`. */
const AS_OF = "2026-10-06T08:15:00+05:30";

/** The cohort curve of the simulator, then a little spread per cohort. 8 weeks wide. */
const CURVE = [9400, 8800, 8400, 8100, 7900, 7700, 7600, 7500];

/**
 * Weekly signup cohorts, oldest first: 17 Aug to 28 Sep, as of the week of 5 Oct. A cohort has a value
 * for week n only when n weeks have passed since it joined; later weeks are null.
 * @param {number} weeks how many offsets each cohort carries
 */
function simulatorCohorts(weeks) {
  return Array.from({ length: 8 }, (_, k) => {
    const reached = 7 - k;
    return {
      week: new Date(Date.UTC(2026, 7, 17) + k * 7 * 86_400_000).toISOString().slice(0, 10),
      households: 200,
      small: false,
      retention_bp: Array.from({ length: weeks }, (_, i) =>
        i < reached ? Math.min(9900, (CURVE[i] ?? 7500) + ((k * 37 + i * 11) % 200) - 60) : null,
      ),
    };
  });
}

/**
 * Weekly Gini of what partners earn, in basis points, oldest first: the kitchens' spread tightened as the
 * fairness weight was raised (the fairness dashboard's story: 0.24 to 0.14).
 */
const GINI_BP = [2800, 2600, 2400, 2100, 1900, 1400];

/** The KPI keys this mock carries (the five tiles); every one is null for the pilot. */
const KEYS = /** @type {const} */ ([
  "active_households",
  "weeks_accepted_bp",
  "waste_g_per_100kg",
  "on_time_bp",
  "partner_earnings_paise",
]);

/** @param {string | null} text a YYYY-MM-DD date, or null @returns {number | null} */
const dayOf = (text) => (text && /^\d{4}-\d{2}-\d{2}$/.test(text) ? Date.parse(text) : null);

/**
 * The query rules every analytics read shares: a data source, and a hub within the caller's grants.
 * @param {Request} request
 * @param {{ staff?: boolean }} [options] hub staff (hub manager, franchise owner) may read; false = admin only
 * @returns {{ denied: Response } | { hubId: string | null, source: "real" | "synthetic", q: URLSearchParams }}
 */
function scope(request, { staff = true } = {}) {
  const q = new URL(request.url).searchParams;
  const role = roleOf(request);
  if (!role) return { denied: problem(401, "auth.unauthenticated", "Sign in again") };
  if (role === "ngo_coordinator" || (!staff && role !== "admin")) {
    return { denied: problem(403, "auth.forbidden", "You do not have access to this") };
  }
  const source = q.get("data_source");
  if (source !== "real" && source !== "synthetic") {
    return {
      denied: problem(422, "analytics.data_source_required", "Choose real or synthetic data", {
        errors: [{ loc: ["query", "data_source"], message: "Field required", type: "missing" }],
      }),
    };
  }
  const hubId = q.get("hub_id");
  if (hubId === null) {
    if (role !== "admin") {
      return { denied: problem(403, "auth.forbidden", "Choose a hub") };
    }
  } else if (!BY_HUB[hubId]) {
    return { denied: problem(404, "hub_ops.hub_not_found", "No such hub") };
  } else if (!visibleHubs(request).some((h) => h.id === hubId)) {
    return { denied: problem(403, "auth.forbidden", "You do not have access to this hub") };
  }
  return { hubId, source, q };
}

/** The societies of the Kharghar hub, as `GET /v1/analytics/societies` lists them. */
const SOCIETIES = [
  ["sunrise-towers", "Sunrise Towers", "live", 54, 20, 50, 4, 9200, 64_000],
  ["krishna-kunj", "Krishna Kunj", "live", 15, 10, 14, 1, 8600, 56_000],
  ["green-valley-heights", "Green Valley Heights", "unlocking", 12, 20, 0, 0, null, null],
  ["shree-ganesh-chs", "Shree Ganesh CHS", "unlocking", 9, 20, 0, 0, null, null],
  ["panchavati-residency", "Panchavati Residency", "prospect", 0, 20, 0, 0, null, null],
].map(([slug, name, status, joined, threshold, active, paused, onTime, box]) => ({
  society_id: SOCIETY_ID[/** @type {keyof typeof SOCIETY_ID} */ (slug)],
  name,
  status,
  joined,
  threshold,
  active_households: active,
  paused_households: paused,
  approved_on_time_bp: onTime,
  average_box_paise: box,
}));

export const analyticsHandlers = [
  // GET /v1/analytics/kpis?hub_id=&from=&to=&grain=week&data_source=
  http.get("*/v1/analytics/kpis", ({ request }) => {
    const s = scope(request);
    if ("denied" in s) return s.denied;
    const { q } = s;
    const grain = q.get("grain") ?? "week";
    if (grain !== "week") {
      return problem(422, "request.invalid", "Only the week grain is mocked", {
        errors: [{ loc: ["query", "grain"], message: "Only week is mocked", type: "value_error" }],
      });
    }
    const from = dayOf(q.get("from"));
    const to = dayOf(q.get("to"));
    if (from !== null && to !== null && to < from) {
      return problem(422, "analytics.range_invalid", "The end date is before the start");
    }
    // The window is `from`..`to`; the mock only uses its length, in whole weeks, ending at the latest week.
    const days = from !== null && to !== null ? (to - from) / 86_400_000 : 84;
    if (days / 7 > 104) {
      return problem(422, "analytics.range_too_long", "Pick a shorter range");
    }
    const count = Math.min(WEEKS.length, Math.max(2, Math.round(days / 7)));
    const periods = WEEKS.slice(-count);
    const series = s.source === "real" ? null : s.hubId === null ? allHubs() : BY_HUB[s.hubId];
    return HttpResponse.json({
      hub_id: s.hubId,
      data_source: s.source,
      grain,
      as_of: AS_OF,
      stale: false,
      periods,
      series: Object.fromEntries(
        KEYS.map((k) => [k, series ? series[k].slice(-count) : periods.map(() => null)]),
      ),
      totals: Object.fromEntries(KEYS.map((k) => [k, series ? series[k].at(-1) : null])),
    });
  }),

  // GET /v1/analytics/societies?hub_id=&data_source=
  http.get("*/v1/analytics/societies", ({ request }) => {
    const s = scope(request);
    if ("denied" in s) return s.denied;
    if (s.hubId === null) return problem(403, "auth.forbidden", "Choose a hub");
    return HttpResponse.json({
      hub_id: s.hubId,
      as_of: AS_OF,
      items: s.source === "real" || s.hubId !== KHARGHAR_HUB_ID ? [] : SOCIETIES,
    });
  }),

  // GET /v1/analytics/partner-earnings?hub_id=&kind=&from=&to=&data_source= : the distribution, never a person
  http.get("*/v1/analytics/partner-earnings", ({ request }) => {
    const s = scope(request);
    if ("denied" in s) return s.denied;
    if (s.hubId === null) return problem(403, "auth.forbidden", "Choose a hub");
    const kind = s.q.get("kind") ?? "kitchen";
    if (!["kitchen", "captain", "rider", "packer"].includes(kind)) {
      return problem(422, "request.invalid", "kind is kitchen, captain, rider or packer", {
        errors: [
          {
            loc: ["query", "kind"],
            message: "Input should be kitchen, captain, rider or packer",
            type: "enum",
          },
        ],
      });
    }
    const from = dayOf(s.q.get("from"));
    const to = dayOf(s.q.get("to"));
    const count =
      from !== null && to !== null
        ? Math.min(GINI_BP.length, Math.max(1, Math.round((to - from) / 604_800_000)))
        : GINI_BP.length;
    const weeks = WEEKS.slice(-count).map((week, i) => {
      const gini = GINI_BP.slice(-count)[i];
      const partners = 5;
      const total = partners * 300_000 + i * 20_000;
      return {
        week,
        partners,
        total_paise: total,
        p10_paise: 180_000 + i * 8000,
        median_paise: 300_000 + i * 4000,
        p90_paise: 460_000 + i * 12_000,
        gini_bp: gini,
      };
    });
    return HttpResponse.json({
      hub_id: s.hubId,
      kind,
      as_of: AS_OF,
      weeks: s.source === "real" ? [] : weeks,
    });
  }),

  // GET /v1/analytics/cohorts?data_source=&hub_id=&weeks=12 (admin)
  http.get("*/v1/analytics/cohorts", ({ request }) => {
    const s = scope(request, { staff: false });
    if ("denied" in s) return s.denied;
    const weeks = Number(s.q.get("weeks") ?? 12);
    if (!Number.isInteger(weeks) || weeks < 4 || weeks > 26) {
      return problem(422, "analytics.range_invalid", "weeks is 4 to 26", {
        errors: [
          {
            loc: ["query", "weeks"],
            message: "Input should be between 4 and 26",
            type: "value_error",
          },
        ],
      });
    }
    return HttpResponse.json({
      data_source: s.source,
      hub_id: s.hubId,
      weeks,
      as_of: AS_OF,
      cohorts: s.source === "real" ? [] : simulatorCohorts(weeks),
    });
  }),
];
