import assert from "node:assert/strict";
import { describe, test } from "node:test";
import { countProblem, donationPoints } from "./kindness.js";

/** @type {import("./kindness.js").PointRate[]} */
const RATES = [
  { code: "clothes", label: "Clothes", unit: "kg", points: 40, max_qty: 15 },
  { code: "school_bag", label: "School bags", unit: "each", points: 50, max_qty: 5 },
  { code: "books", label: "Books", unit: "each", points: 10, max_qty: 30 },
  { code: "toys", label: "Toys", unit: "each", points: 15, max_qty: 10 },
];
const DIWALI = { multiplier: 2, codes: ["clothes", "school_bag", "books", "toys"] };

describe("donationPoints", () => {
  test("a school bag and 3 kg of clothes in a 2x drive earn 340", () => {
    const p = donationPoints({ school_bag: 1, clothes: 3 }, RATES, DIWALI);
    assert.equal(p.total, 340);
    assert.deepEqual(p.lines, { school_bag: 100, clothes: 240 });
  });

  test("the multiplier applies only to the drive's codes", () => {
    const p = donationPoints({ toys: 4, books: 6 }, RATES, { multiplier: 2, codes: ["toys"] });
    assert.deepEqual(p.lines, { toys: 120, books: 60 });
    assert.equal(p.total, 180);
  });

  test("no drive means a multiplier of 1; half kilograms count", () => {
    assert.equal(donationPoints({ clothes: 2.5 }, RATES, null).total, 100);
  });

  test("rounding is half up on the total, and the largest line takes the remainder", () => {
    // 1.5 x (3 books x 10 + 1 toy x 15 = 45) = 67.5 → 68; lines 45 and 22.5 → 23 + ... must add to 68
    const p = donationPoints({ books: 3, toys: 1 }, RATES, {
      multiplier: 1.5,
      codes: ["books", "toys"],
    });
    assert.equal(p.total, 68);
    assert.equal(
      Object.values(p.lines).reduce((n, v) => n + v, 0),
      68,
    );
    assert.equal(p.lines.books, 45);
    assert.equal(p.lines.toys, 23);
  });
});

describe("countProblem", () => {
  const [clothes, bag] = RATES;
  test("units, steps and the cap of twice the rate's maximum", () => {
    assert.equal(countProblem(clothes, 2.5), null);
    assert.equal(countProblem(clothes, 2.3), "In steps of 0.5 kg");
    assert.equal(countProblem(bag, 1.5), "Whole pieces");
    assert.equal(countProblem(bag, -1), "0 or more");
    assert.equal(countProblem(bag, 11), "At most 10");
    assert.equal(countProblem(bag, 10), null);
  });
});
