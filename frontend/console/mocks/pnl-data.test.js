import assert from "node:assert/strict";
import { describe, test } from "node:test";
import { buildPnl, buildUnitEconomics, SEED_ASSUMPTIONS } from "./pnl-data.js";

const sep = /** @type {ReturnType<typeof buildPnl>} */ (buildPnl("2026-09", SEED_ASSUMPTIONS));

describe("September P&L on the default assumptions", () => {
  test("the headline numbers", () => {
    assert.equal(sep.boxes, 702);
    assert.equal(sep.tiffins, 180);
    // 702 boxes x 641.50 + 702 x 4.10 fee + 180 tiffins x 110
    assert.equal(sep.revenue_paise, 702 * 64_150 + 702 * 410 + 180 * 11_000);
    assert.equal(sep.partners_paise.captains, 702 * 600);
    assert.equal(sep.partners_paise.kitchens, 180 * 7000);
  });

  test("the lines add up to the contribution, in exact paise", () => {
    assert.equal(
      sep.lines.reduce((n, l) => n + l.amount_paise, 0),
      sep.contribution_paise,
    );
    assert.ok(sep.lines.every((l) => Number.isSafeInteger(l.amount_paise)));
  });

  test("the weeks add up to the month", () => {
    assert.equal(
      sep.weeks.reduce((n, w) => n + w.contribution_paise, 0),
      sep.contribution_paise,
    );
  });

  test("each line says whether it was measured; the margin and the fee are assumed", () => {
    const source = Object.fromEntries(sep.lines.map((l) => [l.key, l.source]));
    assert.equal(source.produce, "assumed");
    assert.equal(source.fees, "assumed");
    assert.equal(source.acquisition, "assumed");
    assert.equal(source.delivery, "measured");
    assert.equal(source.delivery_fee_revenue, "measured");
  });

  test("box revenue is boxes x the average box, and produce is the share the margin leaves", () => {
    const [box] = sep.lines;
    assert.equal(
      box.basis.kind === "times" && box.basis.qty * box.basis.rate_paise,
      box.amount_paise,
    );
    const produce = sep.lines.find((l) => l.key === "produce");
    assert.deepEqual(produce?.basis, { kind: "share", bp: 7600, of: "box_revenue" });
    assert.equal(produce?.amount_paise, -Math.round((box.amount_paise * 7600) / 10_000));
  });
});

describe("assumptions and months", () => {
  test("a lower margin costs every month the same share of its box revenue", () => {
    const worse = { ...SEED_ASSUMPTIONS, margin_bp: 2000 };
    for (const month of ["2026-09", "2026-08"]) {
      const a = /** @type {NonNullable<typeof sep>} */ (buildPnl(month, SEED_ASSUMPTIONS));
      const b = /** @type {NonNullable<typeof sep>} */ (buildPnl(month, worse));
      const boxRevenue = a.lines[0].amount_paise;
      assert.ok(Math.abs(a.contribution_paise - b.contribution_paise - boxRevenue * 0.04) <= 1);
    }
  });

  test("a delivery assumption does not move a measured month", () => {
    const dearer = { ...SEED_ASSUMPTIONS, delivery_paise: 5000 };
    assert.equal(buildPnl("2026-09", dearer).contribution_paise, sep.contribution_paise);
  });

  test("a month nobody delivered in is all zeros", () => {
    const none = buildPnl("2020-01", SEED_ASSUMPTIONS);
    assert.equal(none.boxes, 0);
    assert.equal(none.revenue_paise, 0);
    assert.equal(none.contribution_paise, 0);
  });
});

describe("unit economics", () => {
  const base = {
    saved: SEED_ASSUMPTIONS,
    weeks: 4,
    prefer: /** @type {const} */ ("measured"),
    overrides: {},
  };

  test("used is override over measured over saved, and sources say which", () => {
    const e = buildUnitEconomics({ ...base, overrides: { margin_bp: 2800 } });
    assert.equal(e.boxes, 702);
    assert.equal(e.used.aov_paise, 64_150);
    assert.equal(e.used.margin_bp, 2800);
    assert.equal(e.used.cac_paise, 1300);
    assert.deepEqual(
      [e.sources.aov_paise, e.sources.margin_bp, e.sources.cac_paise, e.sources.delivery_paise],
      ["measured", "override", "assumed", "measured"],
    );
    assert.equal(e.measured.margin_bp, null);
  });

  test("prefer=assumed ignores what was measured", () => {
    const e = buildUnitEconomics({ ...base, prefer: "assumed" });
    assert.equal(e.used.aov_paise, 62_000);
    assert.equal(e.sources.aov_paise, "assumed");
  });

  test("the box adds the delivery fee as revenue and the weeks use it", () => {
    const e = buildUnitEconomics(base);
    assert.equal(e.delivery_fee_paise, 410);
    assert.equal(e.per_box.delivery_fee_paise, 410);
    const { per_box: b } = e;
    assert.equal(
      b.contribution_paise,
      b.margin_paise +
        b.delivery_fee_paise -
        b.delivery_paise -
        b.packing_paise -
        b.captain_paise -
        b.fee_paise -
        b.cac_paise,
    );
    assert.equal(
      e.weeks.reduce((n, w) => n + w.contribution_paise, 0),
      702 * b.contribution_paise,
    );
  });
});
