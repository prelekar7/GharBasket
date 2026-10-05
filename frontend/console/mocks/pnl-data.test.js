import assert from "node:assert/strict";
import { describe, test } from "node:test";
import { boxEconomics } from "../lib/unit-economics.js";
import { buildPnl, SEED_ASSUMPTIONS } from "./pnl-data.js";

describe("September P&L on the seed assumptions", () => {
  const sep = /** @type {NonNullable<ReturnType<typeof buildPnl>>} */ (
    buildPnl("2026-09", SEED_ASSUMPTIONS)
  );

  test("the headline numbers of the design", () => {
    assert.equal(sep.boxes, 702);
    assert.equal(sep.tiffins, 180);
    assert.equal(sep.revenue_paise, 45_504_000); // ₹4,55,040
    assert.equal(sep.contribution_paise, 4_091_400); // ₹40,914
    assert.equal(sep.captains_paise + sep.kitchens_paise, 1_962_000); // ₹19,620 to local partners
  });

  test("the lines add up to the contribution", () => {
    assert.equal(
      sep.lines.reduce((n, l) => n + l.amount_paise, 0),
      sep.contribution_paise,
    );
  });

  test("the weeks add up to the month", () => {
    assert.equal(
      sep.weeks.reduce((n, w) => n + w.contribution_paise, 0),
      sep.contribution_paise,
    );
  });

  test("box revenue is boxes × the average box", () => {
    const line = sep.lines[0];
    const basis = /** @type {{ qty: number, rate_paise: number }} */ (line.basis);
    assert.equal(basis.qty * basis.rate_paise, line.amount_paise);
  });

  test("the box part earns what the unit-economics calculator says: ₹48.36 × 702 boxes", () => {
    const perBox = boxEconomics(sep.assumptions).contribution_paise;
    assert.equal(perBox, 4836);
    // Boxes plus what the 180 tiffins leave (₹19,800 − ₹12,600 kitchens − 1.2% fee ≈ ₹237.60), within the
    // few rupees the P&L's whole-rupee rounding and the ledger's actual produce cost move it.
    const tiffinPart = 1_980_000 - 1_260_000 - 23_760;
    assert.ok(Math.abs(perBox * 702 + tiffinPart - sep.contribution_paise) <= 300);
  });
});

describe("August and the saved assumptions", () => {
  test("September is ₹6,200 better than August", () => {
    const aug = buildPnl("2026-08", SEED_ASSUMPTIONS);
    const sep = buildPnl("2026-09", SEED_ASSUMPTIONS);
    assert.equal(aug?.contribution_paise, 3_471_400);
    assert.equal((sep?.contribution_paise ?? 0) - (aug?.contribution_paise ?? 0), 620_000);
  });

  test("changing delivery changes the open month but not the closed one", () => {
    const dearer = { ...SEED_ASSUMPTIONS, delivery_paise: 5000 };
    assert.equal(buildPnl("2026-09", dearer)?.contribution_paise, 4_091_400 - 702_000);
    assert.equal(buildPnl("2026-08", dearer)?.contribution_paise, 3_471_400);
  });

  test("an unknown period is null", () => {
    assert.equal(buildPnl("2020-01", SEED_ASSUMPTIONS), null);
  });
});
