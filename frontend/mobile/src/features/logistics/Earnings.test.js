import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import "@/i18n.js";
import { forKind, weekBefore } from "@/features/partners/earnings.js";
import { shortDate } from "@/features/subscriptions/format.js";
import { RiderEarningsView } from "./Earnings.jsx";

/** What GET /v1/earnings answers (EarningsWeekOut, M8 §3.6) for a rider who is also a captain. */
const WEEK = /** @type {any} */ ({
  week: "2026-W41",
  from: "2026-10-05",
  to: "2026-10-11",
  generated_at: "2026-10-11T10:00:00Z",
  total_paise: 80_000 + 600,
  by_kind: [
    { kind: "captain", basis: "bag_delivered", units: 1, amount_paise: 600 },
    { kind: "rider", basis: "dispatched_order", units: 10, amount_paise: 80_000 },
  ],
  days: [
    { date: "2026-10-05", amount_paise: 0 },
    { date: "2026-10-06", amount_paise: 48_600 },
    { date: "2026-10-07", amount_paise: 0 },
    { date: "2026-10-08", amount_paise: 0 },
    { date: "2026-10-09", amount_paise: 32_000 },
    { date: "2026-10-10", amount_paise: 0 },
    { date: "2026-10-11", amount_paise: 0 },
  ],
  entries: [
    ...[0, 1, 2, 3].map((i) => entry("rider", `2026-10-09T0${i}:30:00Z`, 1, 8000, i)),
    ...[0, 1, 2, 3, 4, 5].map((i) => entry("rider", `2026-10-06T0${i}:30:00Z`, 1, 8000, i + 10)),
    entry("captain", "2026-10-06T03:30:00Z", 1, 600, 99),
  ],
  // A payout is listed under the week it pays (billing/earnings.py), so this week has none yet.
  payouts: [],
});

/** Last week's payout: it comes in GET /v1/earnings?week=2026-W40, not in this week's answer. */
const LAST_PAYOUT = /** @type {any} */ ({
  id: "0199c2a5-0000-7000-8000-000000000001",
  partner_id: "0199c2a5-0000-7000-8000-000000000002",
  hub_id: "0199c2a5-0000-7000-8000-000000000003",
  kind: "rider",
  week: "2026-W40",
  period: { from: "2026-09-28", to: "2026-10-04" },
  amount_paise: 64_000,
  status: "processed",
  hold_reason: null,
  provider: "simulated",
  provider_ref: "pout_sim_40r",
  created_at: "2026-10-05T04:30:00Z",
  processed_at: "2026-10-05T04:30:00Z",
});

/** @param {string} kind @param {string} at @param {number} units @param {number} paise @param {number} n */
function entry(kind, at, units, paise, n) {
  return {
    id: `0199c2a5-0000-7000-8000-${String(n).padStart(12, "0")}`,
    kind,
    basis: kind === "rider" ? "dispatched_order" : "bag_delivered",
    units,
    amount_paise: paise,
    ref: { type: "order", id: `0199c2a5-1111-7000-8000-${String(n).padStart(12, "0")}` },
    at,
  };
}

const html = (/** @type {any} */ data, /** @type {any} */ lastPayouts = undefined) =>
  renderToStaticMarkup(createElement(RiderEarningsView, { data, lastPayouts }));

describe("forKind (GET /v1/earnings, EarningsWeekOut)", () => {
  it("keeps one kind's days, units, total and payouts, newest day first", () => {
    const r = forKind(WEEK, "rider");
    expect(r).toMatchObject({ week: "2026-W41", from: "2026-10-05", to: "2026-10-11" });
    expect([r.total_paise, r.units]).toEqual([80_000, 10]);
    expect(r.items).toEqual([
      { date: "2026-10-09", units: 4, amount_paise: 32_000 },
      { date: "2026-10-06", units: 6, amount_paise: 48_000 },
    ]);
    expect(r.payouts).toEqual([]);
    // Last week's answer carries its payout; forKind keeps the rider's only.
    const last = forKind({ ...WEEK, week: "2026-W40", payouts: [LAST_PAYOUT] }, "rider");
    expect(last.payouts.map((p) => p.week)).toEqual(["2026-W40"]);
    expect(forKind({ ...WEEK, payouts: [LAST_PAYOUT] }, "captain").payouts).toEqual([]);
    expect(forKind(WEEK, "captain")).toMatchObject({ total_paise: 600, units: 1 });
  });

  it("answers an empty week and survives a payload that is not the contract", () => {
    for (const bad of [undefined, null, {}, { entries: null }, { items: [] }]) {
      expect(forKind(/** @type {any} */ (bad), "rider")).toMatchObject({
        total_paise: 0,
        units: 0,
        items: [],
        payouts: [],
      });
    }
    // An entry with no usable time is left out instead of throwing.
    const odd = { ...WEEK, entries: [{ ...WEEK.entries[0], at: "soon" }] };
    expect(forKind(/** @type {any} */ (odd), "rider").items).toEqual([]);
  });
});

describe("RiderEarningsView", () => {
  it("draws the contract's week: total, days that paid, and last week's payout by its dates", () => {
    const out = html(forKind(WEEK, "rider"), [LAST_PAYOUT]);
    expect(out).toContain("10 orders dispatched");
    expect(out).toContain("6 orders");
    expect(out).toContain("Paid to your bank");
    expect(out).toContain(
      `Pay for ${shortDate("2026-09-28", "en-IN")} to ${shortDate("2026-10-04", "en-IN")}`,
    );
    expect(out).not.toContain("2026-W40");
    expect(out).toContain("Monday");
  });

  it("says what the rider can do about a held payout", () => {
    const held = { ...LAST_PAYOUT, status: "held", hold_reason: "kyc_incomplete" };
    const out = html(forKind(WEEK, "rider"), [held]);
    expect(out).toContain("On hold");
    expect(out).toContain("Finish your KYC documents to receive it.");
  });

  it("shows a payout once when both weeks' answers carry it", () => {
    const out = html(forKind({ ...WEEK, payouts: [LAST_PAYOUT] }, "rider"), [LAST_PAYOUT]);
    expect(out.split("Paid to your bank").length - 1).toBe(1);
  });

  it("shows the empty state, and does not throw on a missing or odd payload", () => {
    expect(html(forKind({ ...WEEK, entries: [], by_kind: [], payouts: [] }, "rider"))).toContain(
      "Your earnings show here",
    );
    expect(() => html({})).not.toThrow();
    expect(() => html({ items: null, payouts: null })).not.toThrow();
  });
});

describe("weekBefore", () => {
  it("is the ISO week before the one starting on that Monday, across a year end too", () => {
    expect(weekBefore("2026-10-05")).toBe("2026-W40");
    expect(weekBefore("2027-01-04")).toBe("2026-W53");
    expect(weekBefore("2026-01-05")).toBe("2026-W01");
  });
});
