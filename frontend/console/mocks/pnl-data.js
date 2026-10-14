// The franchise P&L and unit economics as the analytics module would compute them (M12 section 3.4: facts
// pulled from the modules, assumptions saved per hub), kept pure so it can be tested without MSW. Money is
// integer paise and every amount is exact: the console rounds for display. A period is the sum of its
// weeks, so the weekly chart always adds up to the headline.
//
// What is measured here (and so ignores the saved assumption unless the owner overrides it): the average box,
// delivery (route cost), packing and captain pay, the pre-live delivery fee, tiffins and the kitchens' pay.
// Not measured: the margin (M6 records no lot costs yet, so produce is the share 10,000 - margin_bp of box
// revenue), the payment fee and acquisition.
import { mulDivRound } from "../lib/money.js";
import { boxEconomics } from "../lib/unit-economics.js";

/** @typedef {import("../lib/unit-economics.js").Assumptions} Assumptions */
/** @typedef {keyof Assumptions} AssumptionKey */

/** The defaults (hub_id null) of section 3.4; the captain's ₹6 a bag is the decided rate. */
export const SEED_ASSUMPTIONS = /** @type {Assumptions} */ ({
  aov_paise: 62_000,
  margin_bp: 2400,
  delivery_paise: 4000,
  packing_paise: 3000,
  captain_paise: 600,
  payment_fee_bp: 120,
  cac_paise: 1300,
});

/** The ranges of section 3.4; outside them the PUT is 422 analytics.assumptions_invalid. */
export const RANGES = /** @type {Record<AssumptionKey, [number, number]>} */ ({
  aov_paise: [10_000, 500_000],
  margin_bp: [0, 6000],
  delivery_paise: [0, 20_000],
  packing_paise: [0, 20_000],
  captain_paise: [0, 5000],
  payment_fee_bp: [0, 500],
  cac_paise: [0, 50_000],
});

/**
 * What one month measured, besides the boxes: route cost, packer and captain pay per box, the pre-live
 * delivery fee per box, the price of a tiffin and what a kitchen earns on it.
 * @typedef {{ delivery_paise: number, packing_paise: number, captain_paise: number,
 *   delivery_fee_paise: number, tiffin_price_paise: number, kitchen_payout_paise: number }} Measured
 * @typedef {{ week: string, boxes: number, aov_paise: number, tiffins: number }} Week
 * @typedef {{ value: string, measured: Measured, weeks: Week[] }} Period
 */

/** @param {string} week @param {number} boxes @param {number} aov_paise @param {number} tiffins @returns {Week} */
const week = (week, boxes, aov_paise, tiffins) => ({ week, boxes, aov_paise, tiffins });

/** @type {Period[]} newest first */
export const PERIODS = [
  {
    value: "2026-09",
    measured: {
      delivery_paise: 3820,
      packing_paise: 400,
      captain_paise: 600,
      delivery_fee_paise: 410,
      tiffin_price_paise: 11_000,
      kitchen_payout_paise: 7000,
    },
    weeks: [
      week("2026-08-31", 144, 64_150, 39),
      week("2026-09-07", 170, 64_150, 44),
      week("2026-09-14", 182, 64_150, 48),
      week("2026-09-21", 206, 64_150, 49),
    ],
  },
  {
    value: "2026-08",
    measured: {
      delivery_paise: 4000,
      packing_paise: 400,
      captain_paise: 600,
      delivery_fee_paise: 450,
      tiffin_price_paise: 11_000,
      kitchen_payout_paise: 7000,
    },
    weeks: [
      week("2026-08-03", 150, 61_000, 34),
      week("2026-08-10", 156, 61_000, 36),
      week("2026-08-17", 162, 61_000, 38),
      week("2026-08-24", 172, 61_000, 42),
    ],
  },
];

/** Every week there is data for, oldest first, with the month's rates. */
const allWeeks = () => [...PERIODS].reverse().flatMap((p) => p.weeks.map((w) => ({ ...w, p })));

/**
 * One week's lines in exact paise (costs negative). Box revenue is boxes x the average box; produce is the
 * share of it that the margin leaves out.
 * @param {Week} w
 * @param {Measured} m
 * @param {Assumptions} a the saved assumptions
 */
function weekLines(w, m, a) {
  const box = w.boxes * w.aov_paise;
  const fee = w.boxes * m.delivery_fee_paise;
  const tiffin = w.tiffins * m.tiffin_price_paise;
  const lines = {
    box_revenue: box,
    delivery_fee_revenue: fee,
    tiffin_revenue: tiffin,
    produce: -mulDivRound(box, 10_000 - a.margin_bp, 10_000),
    kitchen: -w.tiffins * m.kitchen_payout_paise,
    delivery: -w.boxes * m.delivery_paise,
    packing: -w.boxes * m.packing_paise,
    captain: -w.boxes * m.captain_paise,
    fees: -mulDivRound(box + fee + tiffin, a.payment_fee_bp, 10_000),
    acquisition: -w.boxes * a.cac_paise,
  };
  return { ...lines, contribution: Object.values(lines).reduce((x, y) => x + y, 0) };
}

/** @param {string} period "2027-02" @returns {boolean} */
export const isPeriod = (period) => /^\d{4}-(0[1-9]|1[0-2])$/.test(period);

/**
 * The P&L of a month under the hub's saved assumptions; a month nobody delivered in is all zeros.
 * @param {string} periodValue "2026-09"
 * @param {Assumptions} saved
 */
export function buildPnl(periodValue, saved) {
  const p = PERIODS.find((x) => x.value === periodValue);
  const weeks = (p?.weeks ?? []).map((w) => ({
    week: w.week,
    ...weekLines(w, /** @type {Measured} */ (p?.measured), saved),
  }));
  /** @param {keyof ReturnType<typeof weekLines>} key */
  const sum = (key) => weeks.reduce((total, w) => total + w[key], 0);
  const boxes = (p?.weeks ?? []).reduce((n, w) => n + w.boxes, 0);
  const tiffins = (p?.weeks ?? []).reduce((n, w) => n + w.tiffins, 0);
  const m = p?.measured;
  const boxRevenue = sum("box_revenue");
  const feeRevenue = sum("delivery_fee_revenue");
  const tiffinRevenue = sum("tiffin_revenue");
  const revenue = boxRevenue + feeRevenue + tiffinRevenue;
  /** @param {number} qty @param {string} unit @param {number} rate_paise */
  const times = (qty, unit, rate_paise) => ({
    kind: /** @type {const} */ ("times"),
    qty,
    unit,
    rate_paise,
  });
  return {
    period: periodValue,
    boxes,
    tiffins,
    revenue_paise: revenue,
    contribution_paise: sum("contribution"),
    partners_paise: {
      captains: -sum("captain"),
      kitchens: -sum("kitchen"),
      riders: -sum("delivery"),
      packers: -sum("packing"),
    },
    lines: [
      {
        key: "box_revenue",
        amount_paise: boxRevenue,
        source: "measured",
        basis: times(boxes, "box", boxes ? mulDivRound(boxRevenue, 1, boxes) : 0),
      },
      {
        key: "delivery_fee_revenue",
        amount_paise: feeRevenue,
        source: "measured",
        basis: times(boxes, "box", m?.delivery_fee_paise ?? 0),
      },
      {
        key: "tiffin_revenue",
        amount_paise: tiffinRevenue,
        source: "measured",
        basis: times(tiffins, "tiffin", m?.tiffin_price_paise ?? 0),
      },
      {
        key: "produce",
        amount_paise: sum("produce"),
        source: "assumed",
        basis: {
          kind: /** @type {const} */ ("share"),
          bp: 10_000 - saved.margin_bp,
          of: "box_revenue",
        },
      },
      {
        key: "kitchen",
        amount_paise: sum("kitchen"),
        source: "measured",
        basis: times(tiffins, "tiffin", m?.kitchen_payout_paise ?? 0),
      },
      {
        key: "delivery",
        amount_paise: sum("delivery"),
        source: "measured",
        basis: times(boxes, "box", m?.delivery_paise ?? 0),
      },
      {
        key: "packing",
        amount_paise: sum("packing"),
        source: "measured",
        basis: times(boxes, "box", m?.packing_paise ?? 0),
      },
      {
        key: "captain",
        amount_paise: sum("captain"),
        source: "measured",
        basis: times(boxes, "box", m?.captain_paise ?? 0),
      },
      {
        key: "fees",
        amount_paise: sum("fees"),
        source: "assumed",
        basis: { kind: /** @type {const} */ ("share"), bp: saved.payment_fee_bp, of: "revenue" },
      },
      {
        key: "acquisition",
        amount_paise: sum("acquisition"),
        source: "assumed",
        basis: times(boxes, "box", saved.cac_paise),
      },
    ],
    weeks: weeks.map((w) => ({ week: w.week, contribution_paise: w.contribution })),
  };
}

/** @type {AssumptionKey[]} */
export const KEYS = [
  "aov_paise",
  "margin_bp",
  "delivery_paise",
  "packing_paise",
  "captain_paise",
  "payment_fee_bp",
  "cac_paise",
];

/**
 * Unit economics over the last `weeks` weeks of data: what was measured, what is used (override over
 * measured over saved) and the box those work out to.
 * @param {{ saved: Assumptions, weeks: number, prefer: "measured" | "assumed",
 *   overrides: Partial<Assumptions> & { delivery_fee_paise?: number } }} input
 */
export function buildUnitEconomics({ saved, weeks, prefer, overrides }) {
  const window = allWeeks().slice(-weeks);
  const boxes = window.reduce((n, w) => n + w.boxes, 0);
  const latest = PERIODS[0].measured;
  const aov = boxes
    ? mulDivRound(
        window.reduce((n, w) => n + w.boxes * w.aov_paise, 0),
        1,
        boxes,
      )
    : null;
  const measuredFee = boxes
    ? mulDivRound(
        window.reduce((n, w) => n + w.boxes * w.p.measured.delivery_fee_paise, 0),
        1,
        boxes,
      )
    : 0;
  const measured = /** @type {Record<AssumptionKey, number | null>} */ ({
    aov_paise: aov,
    margin_bp: null,
    delivery_paise: boxes ? latest.delivery_paise : null,
    packing_paise: boxes ? latest.packing_paise : null,
    captain_paise: boxes ? latest.captain_paise : null,
    payment_fee_bp: null,
    cac_paise: null,
  });
  const used = /** @type {Assumptions} */ ({});
  const sources = /** @type {Record<AssumptionKey, "measured" | "assumed" | "override">} */ ({});
  for (const k of KEYS) {
    const o = overrides[k];
    const m = measured[k];
    used[k] = o ?? (prefer === "measured" && m != null ? m : saved[k]);
    sources[k] =
      o != null ? "override" : prefer === "measured" && m != null ? "measured" : "assumed";
  }
  const fee = overrides.delivery_fee_paise ?? measuredFee;
  const box = boxEconomics(used, fee);
  return {
    boxes,
    assumptions: saved,
    measured,
    used,
    sources,
    delivery_fee_paise: fee,
    per_box: box,
    weeks: window.map((w) => ({
      week: w.week,
      boxes: w.boxes,
      contribution_paise: w.boxes * box.contribution_paise,
    })),
  };
}
