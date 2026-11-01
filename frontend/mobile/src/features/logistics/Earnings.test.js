import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import "@/i18n.js";
import { forKind } from "@/features/partners/earnings.js";
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
  payouts: [
    {
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
    },
  ],
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

const html = (/** @type {any} */ data) =>
  renderToStaticMarkup(createElement(RiderEarningsView, { data }));

describe("forKind (GET /v1/earnings, EarningsWeekOut)", () => {
  it("keeps one kind's days, units, total and payouts, newest day first", () => {
    const r = forKind(WEEK, "rider");
    expect(r).toMatchObject({ week: "2026-W41", from: "2026-10-05", to: "2026-10-11" });
    expect([r.total_paise, r.units]).toEqual([80_000, 10]);
    expect(r.items).toEqual([
      { date: "2026-10-09", units: 4, amount_paise: 32_000 },
      { date: "2026-10-06", units: 6, amount_paise: 48_000 },
    ]);
    expect(r.payouts.map((p) => p.week)).toEqual(["2026-W40"]);
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
  it("draws the contract's week: total, days that paid and the payout", () => {
    const out = html(forKind(WEEK, "rider"));
    expect(out).toContain("10 orders dispatched");
    expect(out).toContain("6 orders");
    expect(out).toContain("Paid to your bank");
    expect(out).toContain("Week 2026-W40");
    expect(out).toContain("Monday");
  });

  it("shows the empty state, and does not throw on a missing or odd payload", () => {
    expect(html(forKind({ ...WEEK, entries: [], by_kind: [], payouts: [] }, "rider"))).toContain(
      "Your earnings show here",
    );
    expect(() => html({})).not.toThrow();
    expect(() => html({ items: null, payouts: null })).not.toThrow();
  });
});
