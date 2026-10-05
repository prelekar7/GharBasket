// The franchise P&L as the analytics module would compute it (M12: mv_unit_economics over the M5 ledger),
// kept pure so it can be tested without MSW. Money is integer paise; every line is rounded to a whole
// rupee, and a period is the sum of its weeks so the weekly chart always adds up to the headline.
import { mulDivRound, toWholeRupee } from "../lib/money.js";

/** @typedef {import("../lib/unit-economics.js").Assumptions} Assumptions */

/**
 * What the ledger recorded in one delivery week. Produce cost is the vendors' invoices (an actual, not
 * an assumption); everything per order comes from the hub's saved assumptions.
 * @typedef {{ label: string, boxes: number, box_revenue_paise: number, tiffins: number, produce_cost_paise: number }} WeekDrivers
 * @typedef {{ value: string, label: string, kitchen_payout_paise: number, tiffin_price_paise: number,
 *   assumptions: Assumptions | null, weeks: WeekDrivers[] }} PeriodDrivers
 */

/** Saved assumptions on first run: ₹620 box, 24% margin → ₹48.36 a box. */
export const SEED_ASSUMPTIONS = /** @type {Assumptions} */ ({
  aov_paise: 62_000,
  margin_bp: 2400,
  delivery_paise: 4000,
  packing_paise: 3000,
  captain_paise: 1000,
  payment_fee_bp: 120,
  cac_paise: 1300,
});

/**
 * @param {string} label
 * @param {number} boxes
 * @param {number} box_value_paise average value of one box that week
 * @param {number} tiffins
 * @param {number} produce_rupees vendor invoices, whole rupees
 * @returns {WeekDrivers}
 */
const week = (label, boxes, box_value_paise, tiffins, produce_rupees) => ({
  label,
  boxes,
  box_revenue_paise: boxes * box_value_paise,
  tiffins,
  produce_cost_paise: produce_rupees * 100,
});

/** @type {PeriodDrivers[]} newest first; `assumptions: null` means "the hub's saved ones" (the open month). */
export const PERIODS = [
  {
    value: "2026-09",
    label: "September 2026",
    kitchen_payout_paise: 7000,
    tiffin_price_paise: 11_000,
    assumptions: null,
    weeks: [
      week("31 Aug", 144, 62_000, 39, 67_853),
      week("7 Sep", 170, 62_000, 44, 80_104),
      week("14 Sep", 182, 62_000, 48, 85_758),
      week("21 Sep", 206, 62_000, 49, 97_065),
    ],
  },
  {
    value: "2026-08",
    label: "August 2026",
    kitchen_payout_paise: 7000,
    tiffin_price_paise: 11_000,
    assumptions: SEED_ASSUMPTIONS, // August is closed: it keeps the assumptions it ran on
    weeks: [
      week("3 Aug", 150, 61_000, 34, 69_675),
      week("10 Aug", 156, 61_000, 36, 72_462),
      week("17 Aug", 162, 61_000, 38, 75_250),
      week("24 Aug", 172, 61_000, 42, 79_897),
    ],
  },
];

/**
 * One week's lines, each rounded to a whole rupee (costs are negative).
 * @param {WeekDrivers} w
 * @param {PeriodDrivers} p
 * @param {Assumptions} a
 */
function weekLines(w, p, a) {
  const tiffin = w.tiffins * p.tiffin_price_paise;
  const revenue = w.box_revenue_paise + tiffin;
  const lines = {
    box_revenue: w.box_revenue_paise,
    tiffin_revenue: tiffin,
    produce: -w.produce_cost_paise,
    kitchen: -w.tiffins * p.kitchen_payout_paise,
    delivery: -toWholeRupee(w.boxes * a.delivery_paise),
    packing: -toWholeRupee(w.boxes * a.packing_paise),
    captain: -toWholeRupee(w.boxes * a.captain_paise),
    fees: -toWholeRupee(mulDivRound(revenue, a.payment_fee_bp, 10_000)),
    acquisition: -toWholeRupee(w.boxes * a.cac_paise),
  };
  return { ...lines, contribution: Object.values(lines).reduce((x, y) => x + y, 0) };
}

/**
 * The P&L of a period under the hub's saved assumptions (a closed period uses its own).
 * @param {string} periodValue "2026-09"
 * @param {Assumptions} saved
 */
export function buildPnl(periodValue, saved) {
  const p = PERIODS.find((x) => x.value === periodValue);
  if (!p) return null;
  const a = p.assumptions ?? saved;
  const weeks = p.weeks.map((w) => ({ drivers: w, ...weekLines(w, p, a) }));
  /** @param {keyof ReturnType<typeof weekLines>} key */
  const sum = (key) => weeks.reduce((total, w) => total + w[key], 0);
  const boxes = p.weeks.reduce((n, w) => n + w.boxes, 0);
  const tiffins = p.weeks.reduce((n, w) => n + w.tiffins, 0);
  const boxRevenue = sum("box_revenue");
  const tiffinRevenue = sum("tiffin_revenue");
  return {
    period: p.value,
    period_label: p.label,
    boxes,
    tiffins,
    revenue_paise: boxRevenue + tiffinRevenue,
    contribution_paise: sum("contribution"),
    captains_paise: -sum("captain"),
    kitchens_paise: -sum("kitchen"),
    assumptions: a,
    lines: [
      {
        key: "box_revenue",
        label: "Box revenue",
        basis: {
          kind: "times",
          qty: boxes,
          unit: "boxes",
          rate_paise: mulDivRound(boxRevenue, 1, boxes),
          note: "average",
        },
        amount_paise: boxRevenue,
      },
      {
        key: "tiffin_revenue",
        label: "Tiffin revenue",
        basis: { kind: "times", qty: tiffins, unit: "tiffins", rate_paise: p.tiffin_price_paise },
        amount_paise: tiffinRevenue,
      },
      {
        key: "produce",
        label: "Produce cost",
        basis: {
          kind: "share",
          bp: mulDivRound(-sum("produce"), 10_000, boxRevenue),
          of: "box revenue",
        },
        amount_paise: sum("produce"),
      },
      {
        key: "kitchen",
        label: "Kitchen payouts",
        basis: { kind: "times", qty: tiffins, unit: "", rate_paise: p.kitchen_payout_paise },
        amount_paise: sum("kitchen"),
      },
      {
        key: "delivery",
        label: "Delivery",
        basis: { kind: "times", qty: boxes, unit: "", rate_paise: a.delivery_paise },
        amount_paise: sum("delivery"),
      },
      {
        key: "packing",
        label: "Packing",
        basis: { kind: "times", qty: boxes, unit: "", rate_paise: a.packing_paise },
        amount_paise: sum("packing"),
      },
      {
        key: "captain",
        label: "Captain commission",
        basis: { kind: "times", qty: boxes, unit: "", rate_paise: a.captain_paise },
        amount_paise: sum("captain"),
      },
      {
        key: "fees",
        label: "Payment fees",
        basis: { kind: "share", bp: a.payment_fee_bp, of: "revenue" },
        amount_paise: sum("fees"),
      },
      {
        key: "acquisition",
        label: "Customer acquisition",
        basis: { kind: "times", qty: boxes, unit: "", rate_paise: a.cac_paise },
        amount_paise: sum("acquisition"),
      },
    ],
    weeks: weeks.map((w) => ({ label: w.drivers.label, contribution_paise: w.contribution })),
  };
}

/** The months the P&L can show, newest first. */
export const PERIOD_OPTIONS = PERIODS.map((p) => ({ value: p.value, label: p.label }));
