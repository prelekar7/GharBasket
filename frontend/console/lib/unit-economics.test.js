import assert from "node:assert/strict";
import { describe, test } from "node:test";
import {
  formatPaiseExact,
  formatRupees,
  mulDivRound,
  parseDecimal,
  toDecimalText,
  toWholeRupee,
} from "./money.js";
import { boxEconomics, sensitivities } from "./unit-economics.js";

/** The defaults of M12 section 3.4 (the captain's ₹6 a bag is the decided rate, not the mock's old ₹10). */
const SEED = {
  aov_paise: 62_000,
  margin_bp: 2400,
  delivery_paise: 4000,
  packing_paise: 3000,
  captain_paise: 600,
  payment_fee_bp: 120,
  cac_paise: 1300,
};

describe("boxEconomics", () => {
  test("one seed box earns exactly ₹52.36", () => {
    const e = boxEconomics(SEED);
    assert.equal(e.margin_paise, 14_880); // ₹620 × 24%
    assert.equal(e.fee_paise, 744); // ₹620 × 1.2%
    assert.equal(e.delivery_fee_paise, 0);
    assert.equal(e.contribution_paise, 5236);
  });

  test("the pre-live delivery fee is revenue on top of the margin", () => {
    const e = boxEconomics(SEED, 410);
    assert.equal(e.delivery_fee_paise, 410);
    assert.equal(e.contribution_paise, 5236 + 410);
  });

  test("contribution is AOV × margin minus the five per-box costs", () => {
    const e = boxEconomics({ ...SEED, aov_paise: 70_000, margin_bp: 2000, cac_paise: 0 });
    assert.equal(e.contribution_paise, 14_000 - 4000 - 3000 - 600 - 840);
  });

  test("a loss is a negative number, not zero", () => {
    assert.equal(
      boxEconomics({ ...SEED, margin_bp: 0 }).contribution_paise,
      -(4000 + 3000 + 600 + 744 + 1300),
    );
  });

  test("percentage steps round half up to a whole paisa", () => {
    // 1.25% of ₹10.10 = 12.625 paise → 13
    assert.equal(boxEconomics({ ...SEED, aov_paise: 1010, payment_fee_bp: 125 }).fee_paise, 13);
  });
});

describe("sensitivities", () => {
  test("match the rows of the design for the seed assumptions", () => {
    const byKey = Object.fromEntries(sensitivities(SEED).map((s) => [s.key, s.change_paise]));
    assert.deepEqual(byKey, { margin: 1240, aov: 1368, delivery: 1000, cac: -700 });
  });

  test("the delivery fee does not move a what-if", () => {
    const byKey = Object.fromEntries(sensitivities(SEED, 410).map((s) => [s.key, s.change_paise]));
    assert.deepEqual(byKey, { margin: 1240, aov: 1368, delivery: 1000, cac: -700 });
  });
});

describe("money helpers", () => {
  test("mulDivRound rounds half up", () => {
    assert.equal(mulDivRound(62_000, 2400, 10_000), 14_880);
    assert.equal(mulDivRound(5, 1, 2), 3);
    assert.equal(mulDivRound(4, 1, 3), 1);
  });
  test("toWholeRupee", () => {
    assert.equal(toWholeRupee(546_048), 546_000);
    assert.equal(toWholeRupee(546_050), 546_100);
  });
  test("parseDecimal reads text without floats", () => {
    assert.equal(parseDecimal("620"), 62_000);
    assert.equal(parseDecimal("40.5"), 4050);
    assert.equal(parseDecimal("1.2"), 120);
    assert.equal(parseDecimal("1,200"), 120_000);
    assert.equal(parseDecimal("1.234"), null);
    assert.equal(parseDecimal("-5"), null);
    assert.equal(parseDecimal("abc"), null);
    assert.equal(parseDecimal(""), null);
  });
  test("formatPaiseExact always shows two decimals", () => {
    assert.equal(formatPaiseExact(14_880), "₹148.80");
    assert.equal(formatPaiseExact(4000), "₹40.00");
    assert.equal(formatPaiseExact(-700), "−₹7.00");
    assert.equal(formatPaiseExact(123_456_705), "₹12,34,567.05");
  });
  test("formatRupees drops .00 and keeps real paise", () => {
    assert.equal(formatRupees(845_000), "₹8,450");
    assert.equal(formatRupees(4836), "₹48.36");
    assert.equal(formatRupees(-112_000), "−₹1,120");
  });
  test("toDecimalText is the inverse", () => {
    assert.equal(toDecimalText(62_000), "620");
    assert.equal(toDecimalText(4050), "40.5");
    assert.equal(toDecimalText(120), "1.2");
    assert.equal(toDecimalText(5), "0.05");
  });
});
