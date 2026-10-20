import { describe, expect, it } from "vitest";
import en from "@/locales/en-IN.json";
import hi from "@/locales/hi-IN.json";
import mr from "@/locales/mr-IN.json";
import { PILL, priceSwaps, swappedFromPaise } from "./cycle.js";
import { hasBoxOrder, isEditable } from "./queries.js";

const step = { qty: 1, unit: /** @type {const} */ ("piece"), label: "1 pc" };
/** @param {Partial<import("./queries.js").BoxLine>} l @returns {import("./queries.js").BoxLine} */
const line = (l) => ({
  product_id: "p1",
  slug: "lauki",
  name: "Lauki",
  qty: 2,
  step,
  unit_price_paise: 3000,
  total_paise: 6000,
  reason_code: "meal_plan",
  reason_params: null,
  source: "ai",
  replaces: null,
  ...l,
});
const tomato = { product_id: "p2", slug: "tomato", name: "Tomato", unit_price_paise: 5600 };

describe("price swaps from the box", () => {
  it("builds the banner from price_swap lines that say what they replace", () => {
    const cycle = /** @type {any} */ ({
      lines: [
        line({ slug: "onion" }),
        line({
          reason_code: "price_swap",
          reason_params: { from: "tomato", pct: 40 },
          replaces: tomato,
        }),
      ],
    });
    expect(priceSwaps(cycle)).toEqual([
      {
        product_id: "p1",
        item: "Tomato",
        substitute: "Lauki",
        pct: 40,
        keep_delta_paise: 2 * 5600 - 6000,
        original: tomato,
      },
    ]);
  });

  it("copes with a swap that has no percentage, and skips one with nothing replaced", () => {
    const cycle = /** @type {any} */ ({
      lines: [
        line({ reason_code: "price_swap", reason_params: null, replaces: tomato }),
        line({ reason_code: "price_swap", replaces: null }),
      ],
    });
    expect(priceSwaps(cycle)).toHaveLength(1);
    expect(priceSwaps(cycle)[0].pct).toBeNull();
  });

  it("compares a swapped line with what it replaced, per the same number of steps", () => {
    expect(swappedFromPaise(line({ replaces: tomato }))).toBe(11200);
    expect(swappedFromPaise(line({}))).toBeUndefined();
  });
});

describe("when a cycle can be edited", () => {
  it("is open from awaiting_approval to the cut-off, even once approved", () => {
    expect(isEditable({ status: "awaiting_approval" })).toBe(true);
    expect(isEditable({ status: "approved" })).toBe(true);
    for (const status of /** @type {const} */ ([
      "draft",
      "skipped",
      "cancelled",
      "locked",
      "preview",
    ]))
      expect(isEditable({ status })).toBe(false);
    expect(isEditable(null)).toBe(false);
  });
});

describe("when a cycle has a box order to look up", () => {
  it("is so once approved, locked or payment_failed (wallet short at the cut-off)", () => {
    for (const status of /** @type {const} */ (["approved", "locked", "payment_failed"]))
      expect(hasBoxOrder({ status })).toBe(true);
    for (const status of /** @type {const} */ ([
      "draft",
      "awaiting_approval",
      "skipped",
      "cancelled",
    ]))
      expect(hasBoxOrder({ status })).toBe(false);
  });

  it("has a lead line and a pill for payment_failed in every language", () => {
    expect(PILL.payment_failed).toBe("issue");
    for (const lang of [en, hi, mr]) expect(lang.week.lead.payment_failed).toBeTruthy();
  });
});
